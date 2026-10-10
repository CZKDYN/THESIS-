# Software validation record

Development validation updated on 11 October 2026 (Asia/Manila). These are software checks on synthetic data, not physical hardware experiments or thesis accuracy results.

| Check | Observed outcome |
| --- | --- |
| PostgreSQL 17 backend integration suite | 22 passed, 0 failed, 0 skipped. Includes unit removal/restoration, record retention, account/device access, an empty dashboard, and row-lock concurrency for removal versus credits/readings. |
| Portable temporary-SQLite suite | 14 passed, 0 failed, 2 skipped (Phase 1 run before the additional deployment URL test). The two concurrency tests require PostgreSQL and passed there. |
| Real Chromium browser smoke | 9 checks passed: the original dashboard/credit/responsive checks plus removal confirmation/cancel, removal of a filtered unit, archived CSV history, tenant access revocation, restoration, and visible removal actions at 390px. No JavaScript/console errors. |
| Migration upgrade and schema comparison | Unit-archiving revision applied to the existing seeded PostgreSQL deployment-test database; data and accounts were preserved. `flask db check` reports no new upgrade operations. Tests also apply both migrations to fresh databases. |
| Dependency consistency | `pip check` reports no broken requirements. |
| Setup repeatability | Cloud setup rerun preserves `.env` and database. Demo seed rerun reports existing units and preserves balances. |
| Startup lifecycle | Web/simulator started through helpers; repeated startup reuses processes; helper-managed stop/restart validated. PostgreSQL remains independent. |

Deployment preparation also adds a tested normalization of hosted PostgreSQL URL prefixes without dropping TLS options. The Render entrypoint was exercised locally against a fresh PostgreSQL database, with Gunicorn and one simulator; browser results for that run are recorded locally. This is not evidence of a live Render deployment or a completed GitHub-hosted CI run.

The unit-removal run is recorded in `artifacts/unit-removal-tests.xml`; original Phase 1 JUnit records are `artifacts/backend-tests.xml` and `artifacts/portable-tests.xml`. Current browser evidence is `artifacts/browser-smoke.json`, `artifacts/archived-units.png`, and `artifacts/unit-management-mobile.png` in that same ignored directory. Test outputs are local artifacts and are not committed by default. Run the documented commands to generate your own current-run records.

The browser script approves a PHP 100 development load and leaves an auditable sample transaction. This is not proof of a payment, calibrated meter performance, breaker safety, or XGBoost accuracy.

## Chapter 3 evaluation still required

Prepare actual apparatus IDs, reference-meter calibration, timestamped PZEM-versus-reference readings, absolute/relative error calculations, and documented observation conditions. Define forecast data splits, coverage, baselines, and metrics before real-data model trials. Measure estimated-credit-duration error only after a trained forecast exists and the exhaustion/disconnection policy is approved.

Record endurance duration, dropped/replayed packets, Wi-Fi outage start/end, recovery behavior, rejected readings, and database consistency. Physical command acknowledgment/actuation trials require the separately reviewed safety design; no such results are claimed by this software prototype.
