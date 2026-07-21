#!/usr/bin/env bash
set -euo pipefail

ac_root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ac_backend_pid=""
ac_frontend_pid=""

for ac_required in \
  "$ac_root_dir/secrets/admin_password.txt" \
  "$ac_root_dir/secrets/jwt_secret.txt" \
  "$ac_root_dir/backend/requirements.txt" \
  "$ac_root_dir/frontend/package.json"
do
  if [[ ! -f "$ac_required" ]]; then
    echo "Missing required file: $ac_required" >&2
    exit 1
  fi
done

if [[ ! -d "$ac_root_dir/frontend/node_modules" ]]; then
  echo "Missing frontend dependencies. Run: cd frontend && npm install" >&2
  exit 1
fi

for ac_port in 8002 5173; do
  if lsof -nP -iTCP:"$ac_port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $ac_port is already in use. Stop the existing process first." >&2
    exit 1
  fi
done

cleanup() {
  trap - EXIT INT TERM
  if [[ -n "$ac_frontend_pid" ]] && kill -0 "$ac_frontend_pid" 2>/dev/null; then
    kill "$ac_frontend_pid" 2>/dev/null || true
  fi
  if [[ -n "$ac_backend_pid" ]] && kill -0 "$ac_backend_pid" 2>/dev/null; then
    kill "$ac_backend_pid" 2>/dev/null || true
  fi
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

(
  cd "$ac_root_dir/backend"
  exec env \
    DATA_DIR="$ac_root_dir/backend/data" \
    ADMIN_PASSWORD_FILE="$ac_root_dir/secrets/admin_password.txt" \
    JWT_SECRET_FILE="$ac_root_dir/secrets/jwt_secret.txt" \
    PROVIDERS_PATH="$ac_root_dir/providers" \
    WHITELIST_PATH="$ac_root_dir/whitelist/providers.yaml" \
    uvicorn main:app --reload --host 127.0.0.1 --port 8002
) &
ac_backend_pid=$!

(
  cd "$ac_root_dir/frontend"
  exec npm run dev -- --host 127.0.0.1 --port 5173
) &
ac_frontend_pid=$!

echo "Available Computing is starting:"
echo "  Frontend: http://localhost:5173/"
echo "  Backend:  http://localhost:8002/"
echo "Press Ctrl-C to stop both processes."

while kill -0 "$ac_backend_pid" 2>/dev/null && kill -0 "$ac_frontend_pid" 2>/dev/null; do
  sleep 2
done

echo "One of the development processes stopped; shutting down the other." >&2
exit 1
