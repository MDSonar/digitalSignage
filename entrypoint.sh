#!/bin/sh
set -eu

ROLE="${APP_ROLE:-both}"
LOG_DIR="/root/signage/logs"
mkdir -p "$LOG_DIR"

DASH_PID=""
WEB_PID=""

start_dashboard() {
  echo "[entrypoint] Starting dashboard on :5000"
  python /app/dashboard.py &
  DASH_PID=$!
}

start_web() {
  echo "[entrypoint] Starting web player on :8080"
  python /app/web_player.py &
  WEB_PID=$!
}

stop_all() {
  echo "[entrypoint] Stopping..."
  if [ -n "${DASH_PID}" ] && kill -0 "${DASH_PID}" 2>/dev/null; then kill "${DASH_PID}" 2>/dev/null || true; fi
  if [ -n "${WEB_PID}" ] && kill -0 "${WEB_PID}" 2>/dev/null; then kill "${WEB_PID}" 2>/dev/null || true; fi
}

trap stop_all TERM INT

case "$ROLE" in
  dashboard)
    start_dashboard
    wait "${DASH_PID}"
    ;;
  web|web-player)
    start_web
    wait "${WEB_PID}"
    ;;
  both)
    start_dashboard
    start_web
    # Portable wait loop (dash lacks 'wait -n')
    while :; do
      alive=0
      if [ -n "${DASH_PID}" ] && kill -0 "${DASH_PID}" 2>/dev/null; then alive=$((alive+1)); fi
      if [ -n "${WEB_PID}" ] && kill -0 "${WEB_PID}" 2>/dev/null; then alive=$((alive+1)); fi
      [ "$alive" -eq 0 ] && break
      sleep 1
    done
    # Reap children
    wait || true
    ;;
  *)
    echo "[entrypoint] Unknown APP_ROLE: $ROLE (valid: dashboard|web|both)" >&2
    exit 1
    ;;
 esac
