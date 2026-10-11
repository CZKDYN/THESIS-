# DormEnergy

**Development of a Centralized IoT-Based Smart Prepaid Submetering System with Consumption Forecasting for Dormitory Units**

Polytechnic University of the Philippines · Diploma in Electrical Engineering Technology

DormEnergy is a working Flask/PostgreSQL thesis prototype with prepaid accounting, a monitoring-only hardware API, and per-unit XGBoost consumption forecasting. Models trained on demonstration readings are labeled synthetic; real-device accuracy and electrical control require separate experiments.

[![Build and test](https://github.com/CZKDYN/THESIS-/actions/workflows/build.yml/badge.svg)](https://github.com/CZKDYN/THESIS-/actions/workflows/build.yml)
[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https%3A%2F%2Fgithub.com%2FCZKDYN%2FTHESIS-)

## Build and deploy online

**[Open the Render deployment setup](https://render.com/deploy?repo=https%3A%2F%2Fgithub.com%2FCZKDYN%2FTHESIS-)** to deploy the complete demonstration, including PostgreSQL. Sign in to Render and grant access to this GitHub repository. Enter a development-only `DEMO_PASSWORD` of 12–128 characters when prompted, review the plans shown, then deploy. That chosen password is used for `admin@dormenergy.test` and `tenant1@dormenergy.test` through `tenant3@dormenergy.test` on the new hosted database. The local public example password is not automatically embedded in the hosted application.

`render.yaml` configures the pinned Python build, managed PostgreSQL connection, generated session secret, HTTPS cookies, health check, migrations, three seeded units, one simulator, and one forecasting worker alongside Gunicorn. Initial demo models are queued automatically; select a unit and open **Forecasting** to inspect results or request retraining. After deployment succeeds, Render displays the actual public service URL; open its `/login` page. **The setup button is a deployment link, not an already-live website.** Standard hosted PostgreSQL URL prefixes are normalized to the installed psycopg driver while preserving connection options.

The blueprint requests free demo plans. Free web services can sleep when idle, pausing simulation, and free PostgreSQL availability/expiry limits apply. Review Render's current plan terms before using this for a defense or keeping long-term research data; choose an appropriate plan deliberately. No paid service is provisioned by these project files alone. GitHub Actions runs the PostgreSQL test/build workflow on pushes to `main`; inspect [build results](https://github.com/CZKDYN/THESIS-/actions/workflows/build.yml) after pushing.

See [deployment instructions](docs/deployment.md) for build commands, troubleshooting, and the distinction between a simulation demonstration and real occupancy.


## What works

- Shared login, hashed passwords, eight-hour sessions, CSRF protection, login throttling, logout, and server-side administrator/tenant authorization.
- Responsive navy/teal dashboard, mobile sidebar, locally bundled Chart.js, hourly/daily/30-day consumption charts, and 10-second polling.
- Administrator overview, unit registration, tenant account assignment, unit removal/restoration, balances, credit-request approval/rejection, historical rates, transaction export, device monitoring, and notifications.
- Tenant views restricted to their assigned unit, measurements, balance, credit requests, transactions, and notifications. Changing `unit_id` cannot access another unit.
- Three simulated dormitory units, seven days of explicitly synthetic history, a separate five-second simulator, and labeled simulation accounts. No physical hardware is needed.
- Fixed-point decimal billing, cumulative meter deltas, atomic PostgreSQL account updates, duplicate protection, ordered replay, explicit meter-reset baselines, and auditable credit/billing records.
- Scoped hardware bearer authentication, source isolation, range validation, timestamps, gateway arrival versus measurement freshness, and monitoring-only device instructions.
- Per-unit CPU XGBoost training, chronological evaluation, persistence/seasonal baselines, upcoming hourly predictions, comparison charts, and forecast-limited credit-duration estimates. Administrators queue training; tenants see only their unit's results.
- Versioned Alembic migrations for fourteen tables, including database-stored model artifacts and background training jobs. Missing history or recent coverage produces an explicit status instead of invented predictions.

The seed intentionally includes an exhausted sample account to demonstrate a disconnected **simulated** supply. A negative ledger balance records already measured consumption beyond available credit; it is never silently erased or presented as successful physical disconnection.

## Requirements

Python 3.12 (3.11+ supported), PostgreSQL 17 (16+ suitable), Git, and a modern browser. No Node.js or frontend build is needed. Use VS Code with its Python extension and select the `.venv` interpreter. PostgreSQL is the primary database; temporary SQLite is supported only for portable backend tests.

`requirements.lock.txt` pins the tested dependency set, including NumPy, SciPy, and CPU XGBoost. Linux uses `xgboost-cpu` to avoid GPU dependencies; other platforms use the standard XGBoost package in CPU mode. `requirements.txt` lists direct dependencies and `requirements-ml.txt` includes the runtime lockfile. Models use XGBoost's native Booster API and JSON artifacts stored in PostgreSQL. Chart.js 4.4.8 is vendored with its license and was checked against the npm SHA-512 package integrity record.

## Windows setup (PowerShell)

Use the existing checkout, or clone the repository once source has been committed and pushed. Cloud tasks already have isolated checkouts; do not create another Git worktree.

```powershell
cd THESIS-
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock.txt
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
```

Edit `.env` and replace `SECRET_KEY` with the generated random value. Never commit `.env`. If PowerShell activation is unavailable, use `.\.venv\Scripts\python.exe` and `.\.venv\Scripts\flask.exe` directly.

Install PostgreSQL using its official Windows installer, start its service, and open SQL Shell (`psql`) as the PostgreSQL administrator. Choose a local database password and run:

```sql
CREATE ROLE dormenergy WITH LOGIN PASSWORD 'replace-with-your-local-password';
CREATE DATABASE dormenergy OWNER dormenergy;
CREATE DATABASE dormenergy_test OWNER dormenergy;
```

Set `DATABASE_URL=postgresql+psycopg://dormenergy:<url-encoded-local-password>@localhost:5432/dormenergy` in `.env`. Replace the bracketed password and URL-encode reserved characters. Set `SIMULATION_MODE=true`. Keep `SESSION_COOKIE_SECURE=false` for local HTTP.

```powershell
flask --app wsgi db upgrade
$env:DEMO_PASSWORD = 'DormEnergyDemo!2026'
flask --app wsgi seed-demo
Remove-Item Env:DEMO_PASSWORD
flask --app wsgi run --port 5000
```

In a second PowerShell terminal, activate the same virtual environment and run:

```powershell
flask --app wsgi simulate --interval 5
```

In a third terminal, activate the same virtual environment and start forecasting:

```powershell
flask --app wsgi forecast-worker --interval 10 --demo-models
```

Open the local application on port **5000**, at `/login`. Keep the web server, simulator, and forecasting worker running. `seed-demo` preserves existing demo data rather than recreating balances and refuses to seed a database containing non-demo data. You may supply your own development password instead of the public example, or omit `DEMO_PASSWORD` to be prompted securely.

## Demonstration accounts

The current cloud instance was seeded with the following **public, development-only credentials**:

| Role | Email | Password | Scope |
| --- | --- | --- | --- |
| Administrator | `admin@dormenergy.test` | `DormEnergyDemo!2026` | All units |
| Tenant | `tenant1@dormenergy.test` | `DormEnergyDemo!2026` | Unit 01 |
| Tenant | `tenant2@dormenergy.test` | `DormEnergyDemo!2026` | Unit 02 |
| Tenant | `tenant3@dormenergy.test` | `DormEnergyDemo!2026` | Unit 03 |

These are not production accounts. If you supply a different seed password, all four accounts use that password. The demo seed creates one PHP 500 pending request for Unit 02. Browser smoke testing creates additional clearly named sample requests and recorded approvals.

To remove a unit, sign in as an administrator, open **Dormitory units**, click **Remove unit**, and confirm. Removal archives the unit: its balance and history are retained, tenant access and meter updates stop, and pending load requests are rejected. Choose **Archived units** to export retained transaction history or **Restore unit** to reactivate the same unit and tenant account. Restoring does not reopen rejected requests. Archived unit names, device identifiers, and tenant emails remain reserved; removal does not erase records or free them for another account. Physical power switching is unchanged.

## Linux/macOS setup

Create and activate a virtual environment, install `requirements.lock.txt`, create the PostgreSQL role/databases as above, copy `.env.example` to `.env`, and configure your connection and random secret. Then run the same Flask migration, seed, web, simulator, and forecasting-worker commands. The development server works on Windows; Gunicorn is for Linux/cloud deployment.

## This Codex cloud instance

Rootless PostgreSQL 17 binaries, data, socket, logs, and process IDs live outside the checkout at `/workspace/.dormenergy-cloud`. This helper is specific to Debian 13 and uses signed Debian package metadata and verified package downloads. PostgreSQL TCP is bound to loopback with SCRAM authentication; the Unix socket has a private directory and local trust authentication for the isolated development machine. This local superuser connection must not be used in a shared or production deployment.

```bash
cd /workspace/THESIS-
bash scripts/setup-cloud.sh
bash scripts/start-cloud.sh
```

The setup helper preserves `.env`, existing data, source files, migrations, and lockfiles. For an entirely empty development database, set `DEMO_PASSWORD` and run `flask --app wsgi seed-demo`. Services and logs:

- Web application: port 5000; `.dormenergy-cloud/app.log`
- Simulator: five-second interval; `.dormenergy-cloud/simulator.log`
- Forecasting worker: ten-second queue polling and hourly prediction refresh; `.dormenergy-cloud/forecaster.log`
- PostgreSQL: `.dormenergy-cloud/postgres.log`
- Stop only helper-managed web, simulator, and forecasting processes with `bash scripts/stop-cloud.sh`. PostgreSQL remains running.

A filesystem snapshot can retain dependencies and database files, but live processes must restart. Use `start-cloud.sh` for each new cloud task. The repository was initially empty. The source is prepared for the `main` branch in `CZKDYN/THESIS-`; runtime secrets, local database files, and test artifacts are excluded from Git.

## Configuration

| Name | Purpose |
| --- | --- |
| `SECRET_KEY` | Required random session/CSRF signing key. No fallback production key. |
| `DATABASE_URL` | Required SQLAlchemy PostgreSQL URL using `postgresql+psycopg`. |
| `SIMULATION_MODE` | `true` permits the simulator and demo seeding; default `false`. |
| `SESSION_COOKIE_SECURE` | `true` when serving over HTTPS; `false` for local HTTP. |
| `LOW_BALANCE_THRESHOLD` | Initial per-account low-credit warning threshold in PHP; default `100.00`. |
| `DEMO_PASSWORD` | Optional development seed password, not needed after seeding. |
| `TEST_DATABASE_URL` | Optional dedicated test database ending in `_test`. |

All timestamps are stored in UTC. Dashboard charts and displayed records use Asia/Manila. Scheduled rate inputs use the browser's local timezone and are sent as explicit UTC instants.

## Testing

```bash
# Portable test run; isolated temporary SQLite, PostgreSQL-only concurrency checks skipped.
python -m pytest -q

# Required for validating row locks and atomic concurrent updates:
export TEST_DATABASE_URL='postgresql+psycopg://<role>:<password>@localhost:5432/dormenergy_test'
python -m pytest -q --junitxml=artifacts/backend-tests.xml
```

On PowerShell, set `$env:TEST_DATABASE_URL` instead of `export`. The suite applies actual migrations and clears **only** the dedicated test database. Never point it at your development or production database. The cloud connection is `postgresql+psycopg://agent@/dormenergy_test?host=/workspace/.dormenergy-cloud/pgsocket`.

For optional end-to-end browser testing, install Playwright in a separate tooling environment, install its Chromium browser, run the web server, simulator, and forecasting worker, set `DEMO_PASSWORD`, and execute `python scripts/browser_smoke.py`. Set `CHROMIUM_EXECUTABLE` to an existing Chromium binary if needed. This test retrains a synthetic model, approves a PHP 100 **sample** load, and archives/restores a demo unit; use an isolated simulation database. It saves screenshots and a machine-readable outcome in ignored `artifacts/`.

Backend coverage includes authorization, CSRF, decimal billing, ordered readings, unit archival, concurrent financial/device updates, genuine XGBoost training and JSON reload, chronological splits, missing-data/source handling, training jobs, scoped forecasts, and scheduled-rate credit projections. See [testing records](docs/testing.md). Software checks on synthetic data do not establish physical meter accuracy, real forecasting accuracy, or electrical safety.

## Use XGBoost forecasting

Sign in as an administrator, select one active simulated unit, open **Forecasting**, and click **Train XGBoost** if no model exists or you want to retrain. Initial demo jobs are queued automatically by the worker. Ten-second polling displays job progress, held-out MAE/RMSE/MAPE, baselines, comparison charts, and up to 24 upcoming full-hour predictions. Tenants can view their own unit's forecasts but cannot train models.

Training needs at least 168 covered hourly observations for simulation or 720 for separately registered hardware. Stored readings are never relabeled between sources. Recent gaps can leave a trained model waiting for sufficiently covered hours. An unknown credit duration means the forecast cannot support an exhaustion estimate within its horizon. See [forecasting methods and commands](docs/forecasting.md).

## API and hardware integration

See [API contract](docs/api.md) for endpoints, example payloads, authentication, billing rules, and offline replay. The proposed architecture is preserved:

**PZEM-004T V3.0 → ESP32 → Raspberry Pi 4 gateway → Flask → PostgreSQL → dashboard**

Register a **new** hardware unit in the admin workspace, assign a tenant, then provision its token from a trusted terminal:

```bash
flask --app wsgi provision-device ESP32-004
```

The CLI displays the new token once; only its hash is stored. Store it securely on the Raspberry Pi and transmit it over HTTPS. Do not paste it into chat, logs, or version control. Use `X-Device-ID` and a scoped `Authorization: Bearer` header. Rotating a token invalidates the previous token. Simulation devices cannot authenticate as real hardware and cannot be converted by changing a payload.

Set `SIMULATION_MODE=false` and stop the simulator when integrating real devices. Register separate hardware units rather than mixing real measurements with synthetic account history. The frontend reads the same authorized snapshot API in either mode. No ESP32 firmware, Raspberry Pi deployment, or physical MG996R servo control is claimed to be implemented.

## Project structure

```text
dormenergy/
  __init__.py         Application factory, configuration, security headers
  extensions.py       SQLAlchemy, migrations, login, CSRF
  models.py           Relational schema
  web.py              Login, logout, protected dashboard
  api.py              Authorized browser APIs and CSV exports
  hardware.py         Scoped monitoring-only hardware endpoints
  commands.py         Seed, simulator, device-token and forecasting CLI
  services/
    billing.py        Decimal billing and atomic approval
    simulation.py     Explicit synthetic transmissions
    forecasting.py    Hourly coverage, CPU XGBoost, evaluation and projections
  templates/          Login, dashboard, errors, SVG icons
  static/             Custom responsive CSS, JavaScript, local Chart.js
migrations/           Alembic schema revisions
tests/                Backend integration and concurrency checks
scripts/              Cloud lifecycle and optional browser smoke test
docs/                 API, assumptions, testing, deployment, forecasting methods
wsgi.py               Flask entrypoint
```

## Remaining phases

- **Phase 2 refinements:** adviser-reviewed overdraft/disconnection policy, account threshold editing, complete tenant reassignment and password-reset lifecycle, longer-range paginated ledgers. The request/approval, rate, billing, notification, and transaction workflows already function.
- **Phase 3:** ESP32/Raspberry Pi implementation, persistent gateway retry queues, heartbeat monitoring, command/acknowledgment protocol, and hardware reliability testing. The API accepts ordered replay with idempotent event IDs; the physical gateway queue is not yet implemented.
- **Phase 4 refinements:** adviser-reviewed real-data collection, representative seasonal coverage, rolling backtests, recursive multi-hour evaluation, uncertainty estimates, and measured credit-duration error. The training/forecasting prototype is implemented; synthetic results do not establish real-device accuracy. See [forecasting methods](docs/forecasting.md).
- **Phase 5:** calibrated PZEM versus reference-meter trials, endurance tests, Wi-Fi failure/recovery experiments, deployment hardening, backups, and adviser-approved physical safety verification.

See [research assumptions](docs/assumptions.md) and [deployment notes](docs/deployment.md). No thesis PDF was available in the workspace; this implementation follows the supplied master prompt and must be reconciled with Chapter 3 when the PDF is provided.
