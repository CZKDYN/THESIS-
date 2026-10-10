# Build and deploy DormEnergy

## Render demonstration

Deployment setup: https://render.com/deploy?repo=https%3A%2F%2Fgithub.com%2FCZKDYN%2FTHESIS-

1. Sign in to Render and connect `CZKDYN/THESIS-`. For a private repository, authorize Render to read it. The deployment button alone does not make the service live.
2. Render reads `render.yaml` from `main`. It prepares a Python web service and managed PostgreSQL database in the same region. The blueprint requests free demo plans; review the actual plans shown before confirming. Hosting account setup, plan availability, and any billing choice belong to the account owner.
3. Set `DEMO_PASSWORD` in Render's secure setup form to a development password of 12–128 characters. `SECRET_KEY` is generated and `DATABASE_URL` comes from the managed database. Do not put password values in `render.yaml` or commit `.env`.
4. Deploy. The build command is `pip install -r requirements.lock.txt`. The start command is `bash scripts/start-render.sh`. It applies migrations, seeds an empty simulation database once, then supervises Gunicorn and exactly one simulator. Existing demo accounts and balances are preserved during redeploys.
5. When Render reports a healthy deployed service, open its actual public URL at `/login`. Use `admin@dormenergy.test` or `tenant1@dormenergy.test` through `tenant3@dormenergy.test` with the password entered during initial deployment. Changing the seed variable later does not silently change existing account passwords.
6. Verify the admin sees three simulated units, tenants see only their unit, live timestamps advance, and a tenant load request can be approved once. A healthy port alone is not application validation.

The free web service may sleep, stopping the simulator until the app wakes. Gaps are logged without invented consumption. Free PostgreSQL has provider-specific retention/expiry limits; review https://render.com/docs/free and choose a suitable plan or backup strategy for a scheduled thesis defense. No ongoing free hosting guarantee is claimed.

### Troubleshooting

- **Repository inaccessible:** authorize the selected repository in the Render GitHub integration, then retry the Blueprint.
- **Missing or short demo password:** enter a valid `DEMO_PASSWORD` securely and redeploy. The startup script fails clearly instead of waiting on a terminal prompt.
- **Database errors:** verify the linked database is available and the service's `DATABASE_URL` binding points to it. Standard hosted PostgreSQL URL prefixes are normalized to psycopg; TLS query options are preserved.
- **Missing or outdated readings:** check both Gunicorn and simulator output in the service logs. Two web workers share one independent simulator; each worker does not start its own simulator.
- **Expired free database or unavailable plan:** review the provider dashboard and restore a backup or choose an appropriate plan. Creating a new empty database loses old accounts/history and invokes a fresh demonstration seed.

GitHub build workflow: https://github.com/CZKDYN/THESIS-/actions/workflows/build.yml. It installs pinned dependencies, validates the migration, and runs integration/concurrency tests against PostgreSQL 17. A workflow definition does not prove a GitHub-hosted run has passed; check the actual run status.

## Real deployment preparation


The application factory and `wsgi:app` entrypoint can run on a Linux Flask-compatible host with a managed PostgreSQL database. Gunicorn is included; the Blueprint and startup script are prepared, but a completed hosted deployment is claimed only after the provider reports success and the application is checked.

1. Install `requirements.lock.txt` in a Python 3.12 environment. Inject a random `SECRET_KEY` and managed PostgreSQL `DATABASE_URL` through secure hosting settings; use provider-required TLS (`sslmode=verify-full` and the provider CA where supported).
2. Use a least-privilege application role with access only to the application database. Run schema migrations using an appropriately authorized migration identity. Never deploy the cloud helper's local trust/socket configuration.
3. Set `SIMULATION_MODE=false`, `SESSION_COOKIE_SECURE=true`, and use HTTPS. Do not seed or publish public demo accounts into production. Preserve logs without tokens, authorization headers, passwords, or credential URLs.
4. Run `flask --app wsgi db upgrade` as a controlled release step. Do not run `db migrate` automatically in a deployed environment.
5. Start `gunicorn --workers 2 --bind 0.0.0.0:8000 wsgi:app` behind the host's HTTPS routing. The Flask development server is for local development only. Do not enable debug mode.
6. Configure database backups/restoration, request-rate limits, monitoring, and log retention. Verify an authenticated functional request after deployment, not only `/health`.
7. For an intentionally separate demonstration deployment, run one simulator worker only. Do not embed it in each web worker or mix simulation and hardware history.

Before real occupancy: complete password reset/rotation and account lifecycle, production rate limits, auditing/retention policy, gateway retry/heartbeat monitoring, calibration, deployment load tests, and adviser-approved electrical operating procedures. No remote actuator endpoint is enabled in this prototype.
