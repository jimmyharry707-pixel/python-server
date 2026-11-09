#!/usr/bin/env bash
# Run the API (with host-mounted repo) against the same env + Postgres as production.
# Listens on http://127.0.0.1:8000 for local checks; does not replace nginx / app.example.com.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! docker inspect server-api-1 >/dev/null 2>&1; then
  echo "Production container server-api-1 is not running." >&2
  exit 1
fi

echo "Building web UI..."
(cd server/web && npm install && npm run build)

IMG="$(docker inspect server-api-1 --format '{{.Config.Image}}')"
ENV_FILE="$(mktemp)"
trap 'rm -f "$ENV_FILE"' EXIT
docker inspect server-api-1 --format '{{range .Config.Env}}{{println .}}{{end}}' >"$ENV_FILE"

# Always replace previous helper container first.
docker rm -f baymax-stage-dev >/dev/null 2>&1 || true

# Free host :8000 if a non-docker process is still bound (docker-proxy clears with rm above).
if command -v fuser >/dev/null 2>&1; then
  fuser -k 8000/tcp >/dev/null 2>&1 || true
  sleep 1
fi

docker run -d --name baymax-stage-dev \
  --network server_default \
  -p 8000:8000 \
  -v "$ROOT:/app" \
  -w /app \
  --env-file "$ENV_FILE" \
  "$IMG" \
  uvicorn server.main:app --host 0.0.0.0 --port 8000 --reload

echo -n "Waiting for app..."
for _ in $(seq 1 30); do
  if curl -sf -o /dev/null http://127.0.0.1:8000/login; then
    echo " ready"
    echo "App (prod DB/env): http://127.0.0.1:8000/login"
    IPV4="$(hostname -I 2>/dev/null | awk '{print $1}')"
    if [[ -n "${IPV4:-}" ]]; then
      echo "From laptop:       http://${IPV4}:8000/login"
    fi
    echo "Logs: docker logs -f baymax-stage-dev"
    echo "Stop: docker rm -f baymax-stage-dev"
    exit 0
  fi
  echo -n "."
  sleep 0.5
done
echo
echo "Helper container started but /login did not become ready." >&2
docker logs baymax-stage-dev 2>&1 | tail -40 >&2
exit 1
