# Build and deploy DormEnergy

## Render demonstration

Deployment setup: https://render.com/deploy?repo=https%3A%2F%2Fgithub.com%2FCZKDYN%2FTHESIS-

1. Sign in to Render and connect `CZKDYN/THESIS-`. For a private repository, authorize Render to read it. The deployment button alone does not make the service live.
2. Render reads `render.yaml` from `main`. It prepares a Python web service and managed PostgreSQL database in the same region. The blueprint requests free demo plans; review the actual plans shown before confirming. Hosting account setup, plan availability, and any billing choice belong to the account owner.
3. Set `DEMO_PASSWORD` in Render's secure setup form to a development password of 12–128 characters. `SECRET_KEY` is generated and `DATABASE_URL` comes from the managed database. Do not put password values in `render.yaml` or commit `.env`.
4. Deploy. The build command is `pip install -r requirements.lock.txt`. The start command is `bash scripts/start-render.sh`. It applies migrations, seeds an empty simulation database once, then supervises Gunicorn, one simulator, and one forecasting worker. Existing demo accounts, balances, archived units, and database-stored models are preserved during redeploys. Gunicorn defaults to one web worker; `WEB_CONCURRENCY` may be set to 1–4 after measuring the host's memory/load requirements. XGBoost runs on one CPU thread with no GPU dependencies on Linux.
5. When Render reports a healthy deployed service, open its actual public URL at `/login`. Use `admin@dormenergy.test` or `tenant1@dormenergy.test` through `tenant3@dormenergy.test` with the password entered during initial deployment. Changing the seed variable later does not silently change existing account passwords.
6. Verify active units match the retained database, tenants see only their assigned unit, live timestamps advance, and a tenant load request can be approved once. A fresh seed has three units; archived units stay archived. Select one simulated unit and open **Forecasting**. Initial demo jobs are queued automatically; **Train XGBoost** requests retraining or retries a failed job. Verify synthetic labels, genuine held-out metrics, and forecast status. A healthy port alone is not application validation.

For an existing deployment, open the `dormenergy` web service and choose **Manual Deploy → Deploy latest commit** if an automatic deployment has not started. No model file upload or new secret is required for the synthetic demo. Keep the existing `DEMO_PASSWORD`, database binding, and session secret.

The free web service may sleep, stopping both background workers until the app wakes. Gaps are excluded from model targets and cannot become invented zero consumption. The model survives in PostgreSQL, but current predictions may wait for recent hourly coverage after a long sleep. Free PostgreSQL has provider-specific retention/expiry limits; review https://render.com/docs/free and choose a suitable plan or backup strategy for a scheduled thesis defense. No ongoing free hosting guarantee or hosted ML capacity benchmark is claimed.

### Troubleshooting

- **Repository inaccessible:** authorize the selected repository in the Render GitHub integration, then retry the Blueprint.
- **Missing or short demo password:** enter a valid `DEMO_PASSWORD` securely and redeploy. The startup script fails clearly instead of waiting on a terminal prompt.
- **Database errors:** verify the linked database is available and the service's `DATABASE_URL` binding points to it. Standard hosted PostgreSQL URL prefixes are normalized to psycopg; TLS query options are preserved.
- **Missing or outdated readings:** check Gunicorn and simulator output in the service logs. One independent simulator serves all web workers.
- **Training pending or failed:** inspect forecasting-worker logs. Training requires 168 covered simulation hours or 720 hardware hours; source labels cannot be changed to bypass this. A job interrupted for over fifteen minutes becomes retryable. If the host reports memory exhaustion, keep `WEB_CONCURRENCY=1` and assess available capacity.
- **Model trained but no current forecast:** recent covered hours are missing, possibly after hosting sleep. Leave the simulator active and inspect timestamps/coverage. Historical test metrics remain visible; the app does not invent a current forecast.
- **Expired free database or unavailable plan:** review the provider dashboard and restore a backup or choose an appropriate plan. Creating a new empty database loses old accounts/history and invokes a fresh demonstration seed.

GitHub build workflow: https://github.com/CZKDYN/THESIS-/actions/workflows/build.yml. It installs pinned dependencies, validates the migration, and runs integration/concurrency tests against PostgreSQL 17. A workflow definition does not prove a GitHub-hosted run has passed; check the actual run status.

## Real deployment preparation


The application factory and `wsgi:app` entrypoint can run on a Linux Flask-compatible host with a managed PostgreSQL database. Gunicorn is included; the Blueprint and startup script are prepared, but a completed hosted deployment is claimed only after the provider reports success and the application is checked.

1. Install `requirements.lock.txt` in a Python 3.12 environment. Inject a random `SECRET_KEY` and managed PostgreSQL `DATABASE_URL` through secure hosting settings; use provider-required TLS (`sslmode=verify-full` and the provider CA where supported).
2. Use a least-privilege application role with access only to the application database. Run schema migrations using an appropriately authorized migration identity. Never deploy the cloud helper's local trust/socket configuration.
3. Set `SIMULATION_MODE=false`, `SESSION_COOKIE_SECURE=true`, and use HTTPS. Do not seed or publish public demo accounts into production. Preserve logs without tokens, authorization headers, passwords, or credential URLs.
4. Run `flask --app wsgi db upgrade` as a controlled release step. Do not run `db migrate` automatically in a deployed environment.
5. Start `gunicorn --workers 1 --bind 0.0.0.0:8000 wsgi:app` behind the host's HTTPS routing and supervise a separate `flask --app wsgi forecast-worker --interval 10` process. Size the web-worker count using measured capacity. The Flask development server is for local development only. Do not enable debug mode.
6. Configure database backups/restoration, request-rate limits, monitoring, and log retention. Verify an authenticated functional request after deployment, not only `/health`.
7. For an intentionally separate demonstration deployment, run one simulator worker only. Do not embed it in each web worker or mix simulation and hardware history.

Before real occupancy: complete password reset/rotation and account lifecycle, production rate limits, auditing/retention policy, gateway retry/heartbeat monitoring, calibration, deployment load tests, and adviser-approved electrical operating procedures. No remote actuator endpoint is enabled in this prototype.
