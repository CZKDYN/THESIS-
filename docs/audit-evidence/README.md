# Audit measurement evidence

These small JSON files are actual local software-test results, committed for review. All readings, financial amounts and account structures referenced here are **disposable test fixtures**, never real tenants, payments or measurements. No actual credentials, tokens, connection secrets, model artifact or email payload is included.

- `benchmark-before.json`, `benchmark-after.json`: identical three-room/9,999-reading PostgreSQL and 5,000-row SQLite benchmark before/after the performance edits. Short timing runs are affected by host load; the dashboard after-run p95 increased.
- `benchmark-large.json`: 20,000-row SQLite backlog; only 100 local claims, not 20,000 cloud deliveries.
- `network-reliability.json`: actual authenticated MQTT/verified HTTPS/PostgreSQL run, 60-second cloud outage, repair/restart timings and exact ledger reconciliation. Resource numbers cover the parent test process only; injected DB/transport faults are labeled.
- `forecast-benchmark.json`: actual XGBoost on isolated synthetic hourly observations with an occupancy change and gaps. It performs worse than simple baselines; no real-world accuracy claim follows.
- `release-check.json`: disposable local Render entrypoint, supervised child failure/restart, schema check and PostgreSQL dump/restore fixture preservation.
- `dependency-before-summary.json`, `dependency-final.json`: PyPI advisory identities for old pins and exact active-platform final release checks. These are not full OS/pentest/supply-chain scans.
- `tests.json`: final JUnit counts, browser summaries, platform and precise commands/limitations. Original logs, XML, temporary certificates and screenshots stay in ignored local `artifacts/`.

See [validation](../software-validation.md), [audit/ratings](../software-audit.md) and [release readiness](../release-readiness.md) for methods, failures/limits and reproduction. These files are evidence for this reviewed branch; they are not external CI or live Render verification.
