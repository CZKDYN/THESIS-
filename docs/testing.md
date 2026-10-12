# Software validation record

Validated on 12 October 2026 (Asia/Manila). These are software checks in isolated local databases, not physical-meter tests or research accuracy claims.

| Check | Result |
| --- | --- |
| PostgreSQL 17 integration suite | 59 passed, 0 failed, 0 skipped. Includes account setup, forced password changes, session revocation, tenant turnover, source/measurement isolation, actual-rate requirements, device credentials, billing/archival concurrency, and genuine XGBoost training/evaluation/JSON reload. |
| Portable SQLite suite | 53 passed, 0 failed, 6 skipped. The six row-lock concurrency cases require PostgreSQL and passed there. The new account-lifecycle migration and hourly aggregation also ran on SQLite. |
| Email Chromium workflows | 7 checks passed: invitation creation, expiry/resend, mobile activation and scope, delivery status, preferences, recovery/session revocation, and no JavaScript page errors. Memory backend only; zero real emails. |
| Real-mode Chromium workflows | 10 checks passed, 0 console/JavaScript errors. Covers initial owner setup, empty workspace, all navigation, unit editing, tenant creation/change/reset/turnover, tariff and credit workflows, one-time credentials, generated API test readings and billing, exports, archive/restore, insufficient-history forecasting, profile editing, and 390px layouts. |
| Retained-database migration | Counts across thirteen retained tables, account balances, user identities/assignments, and historical rate amounts unchanged. Demo units archived and demo tenant access disabled; pending demo requests rejected by the versioned migration. No records were deleted or relabeled as hardware data. |
| Schema comparison | Actual migration applied; flask db check reports no additional upgrade operations. |
| Dependency/syntax checks | pip check, Python compilation, shell syntax, JavaScript syntax, and Git whitespace checks passed. |
| Deployment entrypoint | Started locally with a legacy SIMULATION_MODE=true supplied deliberately; /health reported hardware mode. One Gunicorn worker, one forecasting worker and one email worker ran, with no simulator or seed step. |

Current backend artifacts are artifacts/email-tests.xml and artifacts/email-portable-tests.xml. Email browser evidence is artifacts/browser-email-smoke.json and email-*.png; preservation evidence is artifacts/email-migration-preservation.json. Existing workflow artifacts include artifacts/browser-real-smoke.json, artifacts/real-migration-preservation.json, artifacts/real-empty-workspace.png, artifacts/real-admin-desktop.png, artifacts/real-units-mobile.png, and artifacts/real-tenant-mobile.png. They are local, ignored by Git, and can be regenerated from documented commands. Earlier demonstration-run artifacts are historical evidence, not evidence of current real-mode operation.

The browser runner creates accounts, test credit and explicitly generated API reading fixtures in a fresh isolated LOCAL database. It refuses non-loopback hosts. Its displayed measurement/billing results verify software behavior only; they do not prove an actual meter connection, physical switching, payment receipt, or real-world XGBoost accuracy. Physical meters were not connected during this task.

Latest Render deployment and GitHub-hosted CI status are not independently verified. Hosted load/memory capacity and fresh cloud-snapshot restoration are also unverified. Source/configuration and successful local runs are separate from hosted deployment evidence.

## Research evaluation still required

Record apparatus IDs, calibration/reference measurements, observation conditions, timestamped comparison data, and absolute/relative errors. Collect representative actual consumption and predefine chronological splits, baselines, recursive multi-hour backtests, duration-estimate error, and acceptance criteria. Software coverage minimums are not sufficient experimental validation.

Evaluate gateway persistence/replay, outages, endurance, host backups/restoration and measured load. Any physical actuator/acknowledgment testing requires its separately implemented hardware design. No such physical results are claimed by the website.

The email migration was also applied to the retained database: all fourteen prior table counts, password hashes, identities/access/session flags, money, device credentials and model artifacts were unchanged; no email was created and .env was unchanged. Resend adapter acceptance/failure/redirect handling is prepared with mocked tests, not a live provider/inbox trial. See [email setup and file inventory](email-system.md).
