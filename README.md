# DormEnergy

**Centralized IoT smart prepaid submetering and consumption forecasting for dormitory units.**

DormEnergy runs in real mode by default. The hosted startup does not create sample accounts or readings and does not start a simulator. Administrators register physical units and tenants; authenticated meters supply consumption. Until a meter communicates, measurements remain unavailable rather than appearing as invented zeroes.

[![Build and test](https://github.com/CZKDYN/THESIS-/actions/workflows/build.yml/badge.svg)](https://github.com/CZKDYN/THESIS-/actions/workflows/build.yml)
[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https%3A%2F%2Fgithub.com%2FCZKDYN%2FTHESIS-)

## Update your existing Render website

Choose **Manual Deploy → Deploy latest commit** for the `dormenergy` web service and wait for Live. Keep the existing PostgreSQL connection and session secret. The startup script forces real mode even if the previous hosting variable says `SIMULATION_MODE=true`; update that setting to `false` for consistency.

Existing administrator credentials remain valid. Sign in, open **Your account**, and set your own name/email and a private password. Previous demo units and tenant logins are retired automatically. Historical meter readings, credit/billing records, models, and balances remain in PostgreSQL. Administrators can export them under **Dormitory units → Previous demonstration history**. These records do not enter live totals or train hardware models.

Register your actual units, confirm the applicable utility rate, assign tenants, and generate device credentials from the website. See [the operating guide](docs/real-mode.md). A real unit may reuse a previous demonstration name; that sample's retained label receives a demo suffix. Real archived units, physical device identifiers, and tenant emails remain reserved to preserve identity/history.

## First installation

Deploy using the Render button above. The Blueprint links PostgreSQL, generates `SECRET_KEY` and `ADMIN_SETUP_TOKEN`, sets HTTPS cookies, and configures health/startup commands. It requests free plans; review the provider's current plan availability and limits before deploying.

On a new empty installation, open `/setup`, enter the **ADMIN_SETUP_TOKEN from your secure Render environment**, and create your own administrator email/password. Do not send the token through chat or commit it. Setup closes after the first active administrator exists. An existing installation with an administrator does not need this token to continue working. The deployment link opens setup; it does not prove a deployment has finished.

Build: `pip install -r requirements.lock.txt`. Start: `bash scripts/start-render.sh`. Startup applies migrations and supervises Gunicorn, XGBoost forecasting, and a separate email outbox worker. Models are stored as JSON in PostgreSQL. Neither worker starts a simulator. Hosted deployment/CI status must be checked in the provider dashboards.

## Website workflows

- Administrator/tenant login, hashed passwords, CSRF, login throttling, scoped authorization, and logout.
- Account name/email editing and password changes. Password changes revoke other sessions.
- Unit registration, name/location editing, low-credit thresholds, archive/restore, and retained CSV history.
- Email-first tenant invitations with single-use activation, optional temporary-password fallback, password recovery, administrator reset, and tenancy termination/replacement. Former accounts retain history and lose access.
- Tenant credit requests, administrator approval/rejection, fixed-point PHP balances, auditable deductions, notifications, and scoped transaction export. Payment is verified manually by the administrator; no payment gateway is included.
- One-time device token generation/replacement/revocation through the administrator UI. The gateway sends scoped authenticated measurements over HTTPS.
- Measurement freshness, consumption charts, connectivity/status monitoring, and database aggregation of historical readings for dashboard refreshes.
- Per-unit CPU XGBoost training jobs, chronological held-out evaluation, baselines, stored hourly predictions, comparison charts, and horizon-limited credit-duration estimates.

Activation and recovery pages need JavaScript to keep secret links out of server URLs. Physical meters/gateway firmware must be connected separately. Monitoring does not send physical breaker commands. XGBoost requires at least **720 covered hourly observations** from the actual unit; it cannot supply real forecasts before data exist. Historical next-hour and recursive multi-hour errors are reported with baselines; they do not guarantee future accuracy or shutoff time.

## Email onboarding and notifications

Select **Send email invitation** when assigning a tenant. The account remains pending until the tenant follows the single-use link and chooses their own password. Administrators see Pending/Activated/Expired and a separate email delivery state, and can resend safely. The temporary-password workflow remains available explicitly as a fallback.

**Forgot Password?** supports existing administrator and eligible tenant accounts without disclosing whether an address exists. Recovery and password changes revoke sessions. Essential security notices stay enabled; users can opt out of balance/load emails in Your account.

Low-balance crossings and approved/rejected load requests create encrypted outbox messages in the same transaction as their billing event. A separate worker sends only committed records, retries temporary Resend failures with a stable idempotency key, and cancels stale tenant/token messages. Provider accepted is distinct from inbox delivery; test-only messages never leave the app.

Real delivery is disabled until you configure Resend. Set `EMAIL_BACKEND=resend`, `RESEND_API_KEY`, `EMAIL_FROM` with a verified sender domain, and `PUBLIC_BASE_URL=https://dormenergy.onrender.com`. A stable `EMAIL_ENCRYPTION_KEY` is recommended. Existing hosting variables are not overwritten by the project. See [the email setup, migration, security, testing and file inventory](docs/email-system.md).

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
# In a third terminal:
flask --app wsgi email-worker --interval 5
```

Create the first administrator through `/setup`. Do not run `seed-demo` for real use. Legacy simulation commands remain only for isolated regression fixtures; normal Render/cloud startup forces real mode and cannot start them.

For the supplied Codex cloud checkout:

```bash
cd /workspace/THESIS-
bash scripts/setup-cloud.sh
bash scripts/start-cloud.sh
```

Rootless PostgreSQL binaries/data and managed process logs live at `/workspace/.dormenergy-cloud`, outside Git. `setup-cloud.sh` uses verified signed Debian packages, installs pinned dependencies, and preserves existing data/secrets. `start-cloud.sh` starts PostgreSQL if needed, applies migrations, and runs the web, forecasting, and email workers. It removes a retained helper-managed simulator from the old setup. `stop-cloud.sh` verifies helper-managed PIDs before stopping them and leaves PostgreSQL running. Processes must restart after snapshots.

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
| `EMAIL_BACKEND` / `RESEND_API_KEY` | Opt-in Resend delivery; default disabled. Test backend uses memory. |
| `EMAIL_FROM` / `PUBLIC_BASE_URL` | Verified sender and explicit canonical HTTPS website origin. |
| `EMAIL_ENCRYPTION_KEY` | Optional dedicated stable Fernet key for queued message encryption. |
| `TEST_DATABASE_URL` | Dedicated test database whose name must end in `_test`. |

Store timestamps in UTC; charts/records display Asia/Manila time. Confirmed utility validity dates require timezone-aware ISO instants.

## Utility rate controls

Electricity rates is locked by default to the owner's **Meralco PHP 14.74/kWh reference** for East Rembo, Taguig. This reference is not independently verified as the latest tariff. It creates no billing rate until the applicable source and validity dates are confirmed through hosting settings. Browser/API rate changes are blocked, old charges are preserved, and prices expire at their recorded period end. See [utility rate configuration](docs/utility-rates.md). For visual review, keep email disabled and utility confirmation false.

## Validation

```bash
# Dedicated PostgreSQL test DB; never use a development/production DB:
TEST_DATABASE_URL='postgresql+psycopg://<role>:<password>@localhost:5432/dormenergy_test' python -m pytest -q --junitxml=artifacts/real-mode-tests.xml
```

On PowerShell, set `$env:TEST_DATABASE_URL`. Portable tests can use temporary SQLite, but PostgreSQL is required for row-lock concurrency validation.

`scripts/browser_real_smoke.py` validates the legacy/manual tariff workflow on a fresh isolated local real-mode deployment using Playwright/Chromium. Set `UTILITY_RATE_LOCKED=false` only on that isolated test deployment and supply `ADMIN_SETUP_TOKEN`, `TEST_OWNER_PASSWORD`, and `TEST_TENANT_PASSWORD`. It creates accounts, approves test credit, injects generated API test readings, changes credentials, and archives/restores units; it refuses non-loopback hosts. Normal locked-rate and HTTPS checks use `python scripts/browser_https_smoke.py` instead. These are software checks, not physical-meter or payment evidence. Artifacts are ignored by Git. See [testing records](docs/testing.md).

## Integration and research

Read [API contract](docs/api.md), [forecasting methods](docs/forecasting.md), [deployment instructions](docs/deployment.md), and [assumptions](docs/assumptions.md). The intended meter chain is PZEM-004T → ESP32 → Raspberry Pi gateway → HTTPS API → PostgreSQL → dashboard.

Remaining work outside the web workflows includes physical firmware/gateway installation, calibrated measurement trials, representative real-data forecast studies, hosted endurance/capacity testing, and any separately designed physical breaker control. Durable gateway retry storage and isolated backup/recovery checks are implemented. No thesis PDF was provided; experimental procedures must be reconciled with the actual Chapter 3 specification.

## Stabilization review

The unmerged `feature/software-stabilization` branch integrates the gateway and website and adds measured correctness/security/performance fixes. Gateway package version: **0.1.1**. See the [audit and engineering ratings](docs/software-audit.md), [test/failure-scenario record](docs/software-validation.md), [committed measurement evidence](docs/audit-evidence/), and [controlled release/rollback checklist](docs/release-readiness.md). Review this branch and its checks before merging or deploying; a branch push does not deploy Render.
