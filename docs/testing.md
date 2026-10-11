# Software validation record

Development validation updated on 11 October 2026 (Asia/Manila). These are software checks on synthetic data, not physical hardware experiments or thesis accuracy results.

| Check | Observed outcome |
| --- | --- |
| PostgreSQL 17 backend integration suite | 30 passed, 0 failed, 0 skipped. Includes billing/archival concurrency, genuine XGBoost training and JSON reload, chronological evaluation, future-endpoint leakage checks, missing coverage, source separation, job recovery, tenant forecast scope, and rate-aware credit projections. |
| Portable temporary-SQLite suite | 14 passed, 0 failed, 2 skipped (Phase 1 run before the additional deployment URL test). The two concurrency tests require PostgreSQL and passed there. |
| Real Chromium browser smoke | 10 checks passed, including administrator XGBoost retraining, completed job state, predictions/evaluation charts, tenant scope, and forecasting at 390px, alongside credit and archive/restore workflows. No JavaScript/console errors. A focused follow-up verified green completed-job styling and no mobile overflow after the final styling change. |
| Migration upgrade and schema comparison | Forecast-model/job revision applied to the retained development database. Counts across its twelve existing tables, account balances, unit archival states, and user assignments were unchanged. `flask db check` reports no new upgrade operations. Backend tests and the isolated deployment apply all revisions to fresh databases. |
| Dependency consistency | `pip check` reports no broken requirements. |
| Setup repeatability | Cloud setup rerun preserves `.env` and database. Demo seed rerun reports existing units and preserves balances. |
| Startup lifecycle | Helper-managed web/simulator processes stopped, retained database upgraded, and web/simulator/forecasting worker restarted. Authenticated readiness and live readings checked. PostgreSQL remains independent. |

Hosted PostgreSQL URL normalization preserves TLS options. The Render entrypoint was exercised locally against a fresh PostgreSQL database with one Gunicorn worker, one simulator, and one forecasting worker; initial demo models trained, and the browser requested successful retraining. This is not evidence that the latest revision has deployed on Render or passed GitHub-hosted CI. Hosted memory/load capacity has not been benchmarked.

Current backend evidence is `artifacts/forecasting-tests.xml`; retained-data checks are `artifacts/forecast-migration-preservation.json`. Browser evidence includes `artifacts/browser-smoke.json`, `artifacts/forecast-desktop.png`, `artifacts/forecast-mobile.png`, `artifacts/archived-units.png`, and `artifacts/unit-management-mobile.png`. Earlier Phase 1/unit-removal records remain separate. These local artifacts are ignored by Git; run the documented commands to generate current evidence.

The browser script approves a PHP 100 development load, retrains a synthetic model, and archives/restores a demo unit. Use an isolated simulation database. These checks verify implementation, not payment receipt, calibrated meter performance, breaker safety, or real-data XGBoost accuracy. The hardware-source training test also uses generated fixtures, not experimental measurements.

## Chapter 3 evaluation still required

Prepare actual apparatus IDs, reference-meter calibration, timestamped PZEM-versus-reference readings, absolute/relative error calculations, and documented observation conditions. Define forecast data splits, coverage, baselines, and metrics before real-data model trials. Measure estimated-credit-duration error only after a trained forecast exists and the exhaustion/disconnection policy is approved.

Record endurance duration, dropped/replayed packets, Wi-Fi outage start/end, recovery behavior, rejected readings, and database consistency. Physical command acknowledgment/actuation trials require the separately reviewed safety design; no such results are claimed by this software prototype.
