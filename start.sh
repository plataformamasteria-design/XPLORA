#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" = "0" ]; then
  mkdir -p /app/storage/recordings /app/storage/perfil-meet /app/storage/data /run/user/10001 /tmp/.X11-unix
  chown -R meetbot:meetbot /app/storage /run/user/10001
  chmod 1777 /tmp/.X11-unix
  exec gosu meetbot "$0" "$@"
fi

export DISPLAY="${DISPLAY:-:99}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/10001}"

# Clean up stale X11 lock files from previous runs
rm -f /tmp/.X99-lock /tmp/.X${DISPLAY#:}-lock 2>/dev/null || true

Xvfb "$DISPLAY" -screen 0 1920x1080x24 -ac +extension RANDR &
pulseaudio --start --exit-idle-time=-1

for attempt in $(seq 1 20); do
  if pactl info >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done

pactl load-module module-null-sink sink_name=meet_output sink_properties=device.description=MeetOutput >/dev/null
pactl set-default-sink meet_output

exec python /app/meet_control.py

