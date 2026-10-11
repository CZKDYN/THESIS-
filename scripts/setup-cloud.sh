#!/usr/bin/env bash
# Rootless setup for this Debian 13 Codex cloud machine; keeps source and local data.
set -euo pipefail
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cloud_state=/workspace/.dormenergy-cloud
pg_bin="$cloud_state/postgres/usr/lib/postgresql/17/bin"
mkdir -p "$cloud_state" "$cloud_state/pgsocket"
chmod 700 "$cloud_state/pgsocket"
if [[ ! -x "$pg_bin/postgres" ]]; then
  if ! [[ -r /usr/share/keyrings/debian-archive-keyring.gpg ]]; then
    echo 'This helper requires Debian 13 and its trusted archive keyring. Use the README on other systems.' >&2
    exit 1
  fi
  mkdir -p "$cloud_state/apt/"{lists/partial,cache/archives/partial,packages,empty}
  cat > "$cloud_state/apt/sources.list" <<'SOURCES'
deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://deb.debian.org/debian trixie main
deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://deb.debian.org/debian-security trixie-security main
SOURCES
  cat > "$cloud_state/apt/apt.conf" <<'CONFIG'
Dir::Etc::main "-";
Dir::Etc::parts "/workspace/.dormenergy-cloud/apt/empty";
Dir::Etc::sourcelist "/workspace/.dormenergy-cloud/apt/sources.list";
Dir::Etc::sourceparts "/workspace/.dormenergy-cloud/apt/empty";
Dir::State::lists "/workspace/.dormenergy-cloud/apt/lists";
Dir::Cache "/workspace/.dormenergy-cloud/apt/cache";
APT::Sandbox::User "agent";
CONFIG
  APT_CONFIG="$cloud_state/apt/apt.conf" /usr/bin/apt-get update
  (
    cd "$cloud_state/apt/packages"
    APT_CONFIG="$cloud_state/apt/apt.conf" /usr/bin/apt-get download postgresql-17 postgresql-client-17
    for package in ./*.deb; do dpkg-deb -x "$package" "$cloud_state/postgres"; done
  )
fi
if [[ ! -f "$cloud_state/pgdata/PG_VERSION" ]]; then
  "$pg_bin/initdb" -D "$cloud_state/pgdata" --auth-local=trust --auth-host=scram-sha-256 --encoding=UTF8 --locale=C.UTF-8
fi
if ! "$pg_bin/pg_ctl" -D "$cloud_state/pgdata" status >/dev/null 2>&1; then
  "$pg_bin/pg_ctl" -D "$cloud_state/pgdata" -l "$cloud_state/postgres.log" -o "-k $cloud_state/pgsocket -p 5432 -h 127.0.0.1" start
fi
for database in dormenergy dormenergy_test; do
  if [[ $("$pg_bin/psql" -h "$cloud_state/pgsocket" -d postgres -tAc "SELECT count(*) FROM pg_database WHERE datname = '$database'") == 0 ]]; then
    "$pg_bin/createdb" -h "$cloud_state/pgsocket" "$database"
  fi
done
cd "$project_root"
if [[ ! -x .venv/bin/python ]]; then python -m venv .venv; fi
.venv/bin/python -m pip install --cache-dir "$cloud_state/pip-cache" -r requirements.lock.txt
if [[ ! -f .env ]]; then
  .venv/bin/python - <<'PY'
import getpass, secrets
from pathlib import Path
path=Path('.env')
path.write_text('SECRET_KEY='+secrets.token_hex(32)+'\nDATABASE_URL=postgresql+psycopg://'+getpass.getuser()+'@/dormenergy?host=/workspace/.dormenergy-cloud/pgsocket\nSIMULATION_MODE=false\nSESSION_COOKIE_SECURE=false\n')
path.chmod(0o600)
PY
fi
.venv/bin/flask --app wsgi db upgrade
printf '%s\n' 'PostgreSQL and dependencies prepared. Existing data and .env preserved.'
