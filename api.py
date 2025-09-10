from flask import Flask, request, jsonify
from github import Github, GithubException
import time
import random
import string
CORS(app)
app = Flask(__name__)

def random_repo_name():
    return "repo-" + ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))

MAIN_YML_CONTENT = """\
name: Android 9 (Docker-Android) — noVNC + Cloudflare URL (Max Speed)

permissions: 
  contents: write

on:
  workflow_dispatch:
    inputs:
      device:
        description: 'Thiết bị mô phỏng'
        required: false
        default: 'Nexus 4'
      width:
        description: 'Chiều rộng màn hình'
        required: false
        default: '720'
      height:
        description: 'Chiều cao màn hình'
        required: false
        default: '1280'
      memory_mb:
        description: 'RAM cho emulator (MB)'
        required: false
        default: '4096'
      cores:
        description: 'Số CPU cores'
        required: false
        default: '4'

env:
  IMAGE_TAG: emulator_9.0
  CONTAINER_NAME: android9
  NOVNC_PORT: 6080
  ADB_PORT: 5555
  DEVICE_NAME: ${{ github.event.inputs.device || 'Nexus 4' }}
  SCREEN_WIDTH: ${{ github.event.inputs.width || '720' }}
  SCREEN_HEIGHT: ${{ github.event.inputs.height || '1280' }}
  RAM_MB: ${{ github.event.inputs.memory_mb || '4096' }}
  CPU_CORES: ${{ github.event.inputs.cores || '4' }}

jobs:
  run:
    runs-on: ubuntu-latest
    timeout-minutes: 360

    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          persist-credentials: true

      - name: Pull Docker-Android (Android 9)
        run: docker pull budtmo/docker-android:${IMAGE_TAG}

      - name: Run Docker-Android (noVNC) optimized for Speedometer
        run: |
          docker rm -f ${CONTAINER_NAME} 2>/dev/null || true
          docker run -d --privileged \
            --name ${CONTAINER_NAME} \
            -p ${NOVNC_PORT}:6080 \
            -p ${ADB_PORT}:5555 \
            -e EMULATOR_DEVICE="${DEVICE_NAME}" \
            -e WEB_VNC=true \
            -e APPIUM=false \
            -e CONNECT_TO_GRID=false \
            -e SCREEN_WIDTH=${SCREEN_WIDTH} \
            -e SCREEN_HEIGHT=${SCREEN_HEIGHT} \
            -e SCREEN_DEPTH=24 \
            -e ANDROID_DISABLE_ANIMATIONS=true \
            -e EMULATOR_ARGS="-memory ${RAM_MB} \
                              -cores ${CPU_CORES} \
                              -gpu swiftshader_indirect \
                              -noaudio \
                              -no-boot-anim \
                              -no-snapshot-load \
                              -no-snapshot-save \
                              -partition-size 2048 \
                              -netdelay none \
                              -netspeed full" \
            --shm-size=2g \
            budtmo/docker-android:${IMAGE_TAG}

          echo "Container started. Waiting initial boot..."
          for i in {1..90}; do
            if curl -fsS http://localhost:${NOVNC_PORT}/ >/dev/null 2>&1; then
              echo "noVNC is up (local) after $i checks."
              break
            fi
            sleep 2
            if [ $i -eq 90 ]; then
              echo "noVNC did not become ready in time"
              exit 1
            fi
          done

      - name: Download Cloudflared (portable)
        run: |
          curl -L --retry 5 --retry-delay 2 \
            https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
            -o cloudflared
          chmod +x cloudflared
          ./cloudflared --version || true

      - name: Start Cloudflare Tunnel → grab URL
        id: tunnel
        run: |
          nohup ./cloudflared tunnel --url http://localhost:${NOVNC_PORT} --no-autoupdate > tunnel.log 2>&1 &
          for i in {1..120}; do
            URL=$(grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' tunnel.log | head -1 || true)
            if [ -n "$URL" ]; then
              echo "url=$URL" >> $GITHUB_OUTPUT
              echo "Public URL: $URL"
              break
            fi
            sleep 1
          done
          if [ -z "$URL" ]; then
            echo "Failed to obtain Cloudflare URL"
            tail -n +1 tunnel.log
            exit 1
          fi

      - name: Recheck origin behind tunnel (avoid 502)
        run: |
          echo "Checking origin via tunnel..."
          for i in {1..40}; do
            if curl -fsS "${{ steps.tunnel.outputs.url }}" >/dev/null 2>&1; then
              echo "Tunnel → origin OK."
              break
            fi
            sleep 3
            if [ $i -eq 40 ]; then
              echo "Origin not reachable via tunnel (possible 502)."
              exit 1
            fi
          done

      - name: Write remote.txt and commit to repo
        run: |
          echo "${{ steps.tunnel.outputs.url }}" > remote.txt
          echo "Device: ${DEVICE_NAME}" >> remote.txt
          echo "Resolution: ${SCREEN_WIDTH}x${SCREEN_HEIGHT}" >> remote.txt
          echo "RAM: ${RAM_MB}MB | Cores: ${CPU_CORES}" >> remote.txt
          echo "Generated: $(date -u +"%Y-%m-%dT%H:%M:%SZ")" >> remote.txt
          git config user.name "github-actions"
          git config user.email "github-actions@users.noreply.github.com"
          git add remote.txt
          git commit -m "update remote.txt (Android 9 URL)" || echo "no changes"
          git push origin HEAD:${GITHUB_REF_NAME}

      - name: Show URL
        run: |
          echo "===================================================="
          echo "Open Android 9 in your browser:"
          cat remote.txt | head -1
          echo "===================================================="

      - name: Keep Alive (up to 6h)
        run: sleep 21600

      - name: Debug — docker & tunnel logs
        if: always()
        run: |
          echo "=== docker ps -a ==="; docker ps -a || true
          echo "=== docker logs (last 200 lines) ==="; docker logs --tail 200 ${CONTAINER_NAME} || true
          echo "=== tunnel.log (last 100 lines) ==="; tail -n 100 tunnel.log || true

"""

@app.route("/create_repo", methods=["POST"])
def create_repo():
    data = request.get_json()
    token = data.get("github_token")
    if not token:
        return jsonify({"error": "Missing github_token"}), 400

    try:
        g = Github(token)
        user = g.get_user()
        repo_name = random_repo_name()
        repo = user.create_repo(repo_name, private=True, auto_init=True)
    except GithubException as e:
        return jsonify({"error": f"GitHub error: {e}"}), 401
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {e}"}), 500

    # Tạo file main.yml trong .github/workflows/
    try:
        repo.create_file(".github/workflows/main.yml", "Add main.yml", MAIN_YML_CONTENT, branch="main")
    except GithubException as e:
        return jsonify({"error": f"Failed to add workflow: {e}"}), 500

    # Trigger workflow bằng commit "dummy"
    try:
        repo.create_file("trigger.txt", "Trigger workflow", "Trigger workflow content", branch="main")
    except GithubException:
        # Nếu file trigger đã tồn tại
        pass

    # Theo dõi file remote.txt
    remote_txt_content = None
    for _ in range(60):  # tối đa 5 phút (60*5s)
        try:
            contents = repo.get_contents("remote.txt")
            remote_txt_content = contents.decoded_content.decode()
            break
        except GithubException:
            time.sleep(5)

    if remote_txt_content:
        return jsonify({"status": "success", "remote_content": remote_txt_content}), 200
    else:
        return jsonify({"error": "remote.txt not found in repo after waiting"}), 500

if __name__ == "__main__":
    app.run(debug=True)
