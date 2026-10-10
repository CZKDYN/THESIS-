#!/usr/bin/env bash
# Stops only PIDs recorded by start-cloud.sh, after verifying their command lines.
set -euo pipefail
cloud_state=/workspace/.dormenergy-cloud
for name in app simulator; do
  pidfile="$cloud_state/$name.pid"
  if [[ -f "$pidfile" ]]; then
    pid=$(cat "$pidfile")
    if [[ "$pid" =~ ^[0-9]+$ ]] && [[ -r "/proc/$pid/cmdline" ]]; then
      command_line=$(tr '\0' ' ' < "/proc/$pid/cmdline")
      expected='--app wsgi run'
      if [[ "$name" == simulator ]]; then expected='--app wsgi simulate'; fi
      if [[ "$command_line" == *"$expected"* ]]; then
        kill "$pid"
      else
        printf '%s\n' "Refusing to stop unrelated process $pid." >&2
        exit 1
      fi
    fi
    rm "$pidfile"
  fi
done
