# Deploy DormEnergy in real mode

## Existing Render service

1. In the dormenergy service choose Manual Deploy → Deploy latest commit. Keep the linked PostgreSQL database and existing SECRET_KEY.
2. Set SIMULATION_MODE=false for clarity. The entrypoint also forces false, so a stale true setting cannot restart simulation. DEMO_PASSWORD is no longer required or used by startup.
3. Wait for Live, open /login, and use the existing administrator credentials. Previous sessions require a fresh sign-in because account session-version checks are now enabled.
4. Open Your account and replace the sample administrator name/email and password with your own. Register real units, configure the actual rate, assign tenants, and provision device credentials through the website.
5. Verify an authenticated workspace. Old demo units/tenants are retired by migration; their records and money remain available as read-only demonstration history. An empty workspace is correct until actual units are registered.

## New deployment

Setup link: https://render.com/deploy?repo=https%3A%2F%2Fgithub.com%2FCZKDYN%2FTHESIS-

The Blueprint provisions a Python web service and managed PostgreSQL, generates SECRET_KEY and ADMIN_SETUP_TOKEN, sets real mode and secure cookies, and registers /health. Review the plans displayed before confirming. The configuration requests free plans; no ongoing availability guarantee is made.

After the service is Live, open /setup and enter the ADMIN_SETUP_TOKEN from the secure hosting environment. Create the first administrator with a private password. The setup route closes once an active administrator exists. Do not paste the token into chat, URLs, logs, or Git. If the token is absent on a completely new deployment, add it securely to Render and redeploy. Existing administrators do not need a setup token.

Build command: `pip install -r requirements.lock.txt`. Start command: `bash scripts/start-render.sh`. Startup applies versioned migrations, then supervises one Gunicorn worker and one forecasting worker. It never seeds or starts a simulator. WEB_CONCURRENCY can be 1–4; measure memory/load before increasing it. Linux installs CPU-only XGBoost, using one thread for training. Models/forecasts persist in PostgreSQL, not ephemeral host storage.

The free service may sleep, pausing forecast jobs and making physical gateway requests wait for wakeup. A real gateway needs durable retry/replay storage and original timestamps/event IDs. Configure appropriate availability and PostgreSQL backups/retention for actual operation. Hosted memory/load capacity has not been benchmarked.

## Troubleshoot

- Database/startup errors: inspect migrations and the linked DATABASE_URL. Standard postgres/postgresql prefixes are normalized to psycopg while preserving TLS options.
- Empty dashboard: register actual units. The retained demonstration workspace is intentionally excluded.
- Sign-in redirects to password change: replace the temporary tenant password before using other routes.
- Device 401: verify the registered identifier and current scoped token; rotation/revocation invalidates the old token.
- Device 422 about a rate: configure an actual rate covering the reading interval. A retired demonstration tariff cannot cover real use.
- No live measurements: inspect receipt/measurement timestamps and gateway delivery. The website does not generate replacement readings.
- Forecast unavailable: collect 720 covered hardware hours and request training. Failed/pending jobs are visible in the dashboard and forecasting-worker logs. The worker checks jobs every ten seconds; interrupted running jobs become retryable after fifteen minutes.

GitHub build results: https://github.com/CZKDYN/THESIS-/actions/workflows/build.yml. A workflow definition or push is not evidence that hosted CI/deployment has passed. The latest deployment must be verified in Render.

## Other Linux hosts

Use Python 3.12, the pinned lockfile, PostgreSQL, HTTPS, random signing/setup secrets, and secure cookies. Use a least-privilege database role and provider-supported TLS verification. Apply `flask --app wsgi db upgrade` as a controlled release step. Supervise Gunicorn and a separate `flask --app wsgi forecast-worker --interval 10` process. Do not enable debug mode or run the development server publicly.

The Codex helper's private local trust socket is for an isolated development machine, not a hosting configuration. Physical firmware/actuation, payment processing, hosting redundancy, and experimental calibration are separate from deployment of these web workflows.
