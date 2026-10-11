# DormEnergy

**Centralized IoT smart prepaid submetering and consumption forecasting for dormitory units.**

DormEnergy runs in real mode by default. The hosted startup does not create sample accounts or readings and does not start a simulator. Administrators register physical units and tenants; authenticated meters supply consumption. Until a meter communicates, measurements remain unavailable rather than appearing as invented zeroes.

[![Build and test](https://github.com/CZKDYN/THESIS-/actions/workflows/build.yml/badge.svg)](https://github.com/CZKDYN/THESIS-/actions/workflows/build.yml)
[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https%3A%2F%2Fgithub.com%2FCZKDYN%2FTHESIS-)

## Update your existing Render website

Choose **Manual Deploy → Deploy latest commit** for the `dormenergy` web service and wait for Live. Keep the existing PostgreSQL connection and session secret. The startup script forces real mode even if the previous hosting variable says `SIMULATION_MODE=true`; update that setting to `false` for consistency.

Existing administrator credentials remain valid. Sign in, open **Your account**, and set your own name/email and a private password. Previous demo units and tenant logins are retired automatically. Historical meter readings, credit/billing records, models, and balances remain in PostgreSQL. Administrators can export them under **Dormitory units → Previous demonstration history**. These records do not enter live totals or train hardware models.

Register your actual units, set the electricity rate, assign tenants, and generate device credentials from the website. See [the operating guide](docs/real-mode.md). A real unit may reuse a previous demonstration name; that sample's retained label receives a demo suffix. Real archived units, physical device identifiers, and tenant emails remain reserved to preserve identity/history.

## First installation

Deploy using the Render button above. The Blueprint links PostgreSQL, generates `SECRET_KEY` and `ADMIN_SETUP_TOKEN`, sets HTTPS cookies, and configures health/startup commands. It requests free plans; review the provider's current plan availability and limits before deploying.

On a new empty installation, open `/setup`, enter the **ADMIN_SETUP_TOKEN from your secure Render environment**, and create your own administrator email/password. Do not send the token through chat or commit it. Setup closes after the first active administrator exists. An existing installation with an administrator does not need this token to continue working. The deployment link opens setup; it does not prove a deployment has finished.

Build: `pip install -r requirements.lock.txt`. Start: `bash scripts/start-render.sh`. Startup applies migrations and supervises one Gunicorn worker and one XGBoost forecasting worker. Models are stored as JSON in PostgreSQL. Neither worker starts a simulator. Hosted deployment/CI status must be checked in the provider dashboards.

## Website workflows

- Administrator/tenant login, hashed passwords, CSRF, login throttling, scoped authorization, and logout.
- Account name/email editing and password changes. Password changes revoke other sessions.
- Unit registration, name/location editing, low-credit thresholds, archive/restore, and retained CSV history.
- Tenant assignment, mandatory first-login password changes, administrator password reset, and tenancy termination/replacement. Former accounts retain history and lose access.
- Tenant credit requests, administrator approval/rejection, fixed-point PHP balances, auditable deductions, notifications, and scoped transaction export. Payment is verified manually by the administrator; no payment gateway is included.
- One-time device token generation/replacement/revocation through the administrator UI. The gateway sends scoped authenticated measurements over HTTPS.
- Measurement freshness, consumption charts, connectivity/status monitoring, and database aggregation of historical readings for dashboard refreshes.
- Per-unit CPU XGBoost training jobs, chronological held-out evaluation, baselines, stored hourly predictions, comparison charts, and horizon-limited credit-duration estimates.

Physical meters/gateway firmware must be connected separately. Monitoring does not send physical breaker commands. XGBoost requires at least **720 covered hourly observations** from the actual unit; it cannot supply real forecasts before data exist. Forecast metrics are measured next-hour errors, not a guarantee of 24-hour accuracy or shutoff time.

## Local setup

Use Python 3.12, PostgreSQL 17 (16+ suitable), Git, and a modern browser. No Node/frontend build is required. Linux uses the pinned CPU-only XGBoost package; other platforms use standard XGBoost in CPU mode. Chart.js is bundled locally with its license.

```bash
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# PowerShell instead: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock.txt
```

Copy `.env.example` to `.env`. Configure your own PostgreSQL database/role, random signing key, and a random administrator setup token. Keep `SIMULATION_MODE=false`; use secure cookies on HTTPS and `false` only for local HTTP. Never commit `.env` or credential values.

```bash
flask --app wsgi db upgrade
flask --app wsgi run --port 5000
# In another activated terminal:
flask --app wsgi forecast-worker --interval 10
```

Create the first administrator through `/setup`. Do not run `seed-demo` for real use. Legacy simulation commands remain only for isolated regression fixtures; normal Render/cloud startup forces real mode and cannot start them.

For the supplied Codex cloud checkout:

```bash
cd /workspace/THESIS-
bash scripts/setup-cloud.sh
bash scripts/start-cloud.sh
```

Rootless PostgreSQL binaries/data and managed process logs live at `/workspace/.dormenergy-cloud`, outside Git. `setup-cloud.sh` uses verified signed Debian packages, installs pinned dependencies, and preserves existing data/secrets. `start-cloud.sh` starts PostgreSQL if needed, applies migrations, and runs the web/forecasting workers. It removes a retained helper-managed simulator from the old setup. `stop-cloud.sh` verifies helper-managed PIDs before stopping them and leaves PostgreSQL running. Processes must restart after snapshots.

## Configuration

| Name | Purpose |
| --- | --- |
| `SECRET_KEY` | Required random session/CSRF signing key. |
| `DATABASE_URL` | PostgreSQL SQLAlchemy URL; hosted PostgreSQL prefixes are normalized to psycopg while preserving TLS options. |
| `ADMIN_SETUP_TOKEN` | Hosting secret required only for first-administrator setup on an empty installation. |
| `SIMULATION_MODE` | Default `false`; deployment startup forces `false`. |
| `SESSION_COOKIE_SECURE` | `true` for HTTPS; `false` only for local HTTP. |
| `LOW_BALANCE_THRESHOLD` | Initial per-unit PHP threshold; default 100.00, editable in the website. |
| `WEB_CONCURRENCY` | Render web-worker count, 1–4; default one. Size after measuring capacity. |
| `TEST_DATABASE_URL` | Dedicated test database whose name must end in `_test`. |

Store timestamps in UTC; charts/records display Asia/Manila time. Scheduled rate inputs use the browser's timezone and are sent as UTC instants.

## Validation

```bash
# Dedicated PostgreSQL test DB; never use a development/production DB:
TEST_DATABASE_URL='postgresql+psycopg://<role>:<password>@localhost:5432/dormenergy_test' python -m pytest -q --junitxml=artifacts/real-mode-tests.xml
```

On PowerShell, set `$env:TEST_DATABASE_URL`. Portable tests can use temporary SQLite, but PostgreSQL is required for row-lock concurrency validation.

`scripts/browser_real_smoke.py` validates a fresh isolated local real-mode deployment using Playwright/Chromium. It creates accounts, approves test credit, injects explicitly generated API test readings, changes credentials, and archives/restores units. It refuses non-loopback hosts. Supply `ADMIN_SETUP_TOKEN`, `TEST_OWNER_PASSWORD`, and `TEST_TENANT_PASSWORD` for that isolated test only. These are software checks, not physical-meter or payment evidence. Artifacts are ignored by Git. See [testing records](docs/testing.md).

## Integration and research

Read [API contract](docs/api.md), [forecasting methods](docs/forecasting.md), [deployment instructions](docs/deployment.md), and [assumptions](docs/assumptions.md). The intended meter chain is PZEM-004T → ESP32 → Raspberry Pi gateway → HTTPS API → PostgreSQL → dashboard.

Remaining work outside the web workflows includes physical firmware/gateway installation, gateway retry storage, calibrated measurement trials, real-data forecast backtests, backup/load testing, and any separately designed physical breaker control. No thesis PDF was provided; experimental procedures must be reconciled with the actual Chapter 3 specification.
