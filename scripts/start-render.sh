#!/usr/bin/env bash
# Production WSGI entrypoint for the explicitly simulated thesis demonstration.
# One forecasting worker and, in simulation mode, one simulator run alongside Gunicorn.
set -euo pipefail
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
: "${DATABASE_URL:?Set DATABASE_URL to the managed PostgreSQL connection string.}"
: "${SECRET_KEY:?Set a generated random SECRET_KEY.}"
port="${PORT:-10000}"
if ! [[ "$port" =~ ^[0-9]+$ ]] || (( port < 1 || port > 65535 )); then
  printf '%s\n' 'PORT must be a number from 1 to 65535.' >&2
  exit 1
fi
simulating=$(python -c "from dormenergy import create_app; print('yes' if create_app().config['SIMULATION_MODE'] else 'no')")
if [[ "$simulating" == yes ]]; then
  : "${DEMO_PASSWORD:?Set a development-only DEMO_PASSWORD with at least 12 characters in Render settings.}"
  if (( ${#DEMO_PASSWORD} < 12 || ${#DEMO_PASSWORD} > 128 )); then
    printf '%s\n' 'DEMO_PASSWORD must contain 12–128 characters.' >&2
    exit 1
  fi
fi
python -m flask --app wsgi db upgrade
if [[ "$simulating" == yes ]]; then
  python -m flask --app wsgi seed-demo < /dev/null
fi
web_pid=''
simulator_pid=''
forecast_pid=''
cleanup() {
  trap - EXIT
  for pid in "$web_pid" "$simulator_pid" "$forecast_pid"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then kill -TERM "$pid" 2>/dev/null || true; fi
  done
  for pid in "$web_pid" "$simulator_pid" "$forecast_pid"; do
    if [[ -n "$pid" ]]; then wait "$pid" 2>/dev/null || true; fi
  done
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
workers="${WEB_CONCURRENCY:-1}"
if ! [[ "$workers" =~ ^[1-4]$ ]]; then
  printf '%s\n' 'WEB_CONCURRENCY must be between 1 and 4.' >&2
  exit 1
fi
python -m gunicorn --workers "$workers" --bind "0.0.0.0:$port" --access-logfile - --error-logfile - --timeout 30 wsgi:app &
web_pid=$!
if [[ "$simulating" == yes ]]; then
  python -m flask --app wsgi simulate --interval 5 &
  simulator_pid=$!
fi
python -m flask --app wsgi forecast-worker --interval 10 --demo-models &
forecast_pid=$!
# Fail if any required child exits, allowing the host to restart the service.
set +e
if [[ -n "$simulator_pid" ]]; then
  wait -n "$web_pid" "$simulator_pid" "$forecast_pid"
else
  wait -n "$web_pid" "$forecast_pid"
fi
status=$?
set -e
if (( status == 0 )); then status=1; fi
exit "$status"
