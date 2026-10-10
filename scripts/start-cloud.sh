#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cloud_state=/workspace/.dormenergy-cloud
pg_bin="$cloud_state/postgres/usr/lib/postgresql/17/bin"
cd "$project_root"
mkdir -p "$cloud_state"
if ! "$pg_bin/pg_ctl" -D "$cloud_state/pgdata" status >/dev/null 2>&1; then
  "$pg_bin/pg_ctl" -D "$cloud_state/pgdata" -l "$cloud_state/postgres.log" -o "-k $cloud_state/pgsocket -p 5432 -h 127.0.0.1" start
fi
.venv/bin/flask --app wsgi db upgrade
start_process() {
  local name="$1"
  shift
  local pidfile="$cloud_state/$name.pid"
  if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    printf '%s\n' "$name already running."
    return
  fi
  nohup "$@" > "$cloud_state/$name.log" 2>&1 < /dev/null &
  local pid=$!
  printf '%s\n' "$pid" > "$pidfile"
  sleep 1
  if ! kill -0 "$pid" 2>/dev/null; then
    printf '%s\n' "$name failed; inspect $cloud_state/$name.log" >&2
    exit 1
  fi
}
start_process app .venv/bin/flask --app wsgi run --host 0.0.0.0 --port 5000
simulation_enabled=$(.venv/bin/python -c "from dormenergy import create_app; print('yes' if create_app().config['SIMULATION_MODE'] else 'no')")
if [[ "$simulation_enabled" == yes ]]; then
  start_process simulator .venv/bin/flask --app wsgi simulate --interval 5
fi
curl --fail --silent --show-error http://127.0.0.1:5000/health
printf '\n%s\n' 'Development services started. Validate an authenticated dashboard; health alone is not a functional check.'
