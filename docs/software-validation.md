# Stabilization validation and reproducible software demonstration

Review branch: `feature/software-stabilization`, gateway 0.1.1. Local platform: Linux x86_64, Python 3.12.14, PostgreSQL 17, Mosquitto 2.0.21, Chromium/Playwright 1.62. No production database, hosted readings or actual email provider was used. [Audit/ratings](software-audit.md) and [release gates](release-readiness.md) explain what these results support.

## Fresh execution record

| Check | Actual final result | Evidence |
| --- | --- | --- |
| Baseline backend before fixes | 87 passed, zero skipped | `artifacts/audit/baseline-backend.xml` |
| Baseline gateway before fixes | 59 passed, zero skipped | `artifacts/audit/baseline-gateway.xml` |
| Final PostgreSQL backend | **127 passed**, zero failed/skipped, 77.11 s | `artifacts/audit/backend-final.xml` |
| Final portable SQLite backend | **118 passed, 9 skipped**, zero failed, 68.67 s | `artifacts/audit/portable-final.xml`; skips are actual PostgreSQL locking cases, not successes |
| Final gateway unit regressions | **68 passed**, zero failed/skipped | `artifacts/audit/gateway-final.xml` |
| Opted-in native MQTT/TLS/HTTPS/PostgreSQL integration | **1 passed**, zero failed/skipped, 130.09 s | `artifacts/audit/network-final.xml`, [network metrics](audit-evidence/network-reliability.json) |
| Chromium inside network integration | Owner + three tenants, correct balances/unit scopes, four views, zero script errors | Four local `artifacts/gateway-dashboard-*.png` files |
| Secure HTTPS account/utility browser | **8 checks passed**, zero script errors/real emails | `artifacts/browser-https-smoke.json` |
| Synthetic forecast browser | **4 checks passed**, both charts, recursive metrics/source labels, phone layout, zero script errors | `artifacts/audit/forecast-browser.json`, desktop/mobile images |
| New forecast regressions after finite/thread fixes | 14 passed | `artifacts/audit/forecast-final.xml`; included in final 127, not added again |
| Local Render entrypoint / worker failure / restart | Passed; real mode despite supplied legacy true, all three roles, child exit causes nonzero parent exit, restart/schema check, zero automatic readings | [release-check.json](audit-evidence/release-check.json) |
| Disposable PostgreSQL backup → distinct restore DB | Passed; all 17 table counts, schema head and fixture financial values preserved | Same release evidence; both test DBs cleared afterward |
| Patched editable gateway and wheel | Passed; 0.1.1 wheel built, pip check clean | `artifacts/audit/gateway-wheel/`, build/install logs |
| PyPI exact active-platform dependency advisory check | **35 checked, zero unavailable, zero advisory-listed pins** | [dependency-final.json](audit-evidence/dependency-final.json) |
| Linux Python compilation / Bash syntax / Git whitespace | Passed | Reproducible commands below |
| GitHub-hosted Actions, Windows, Python 3.11, Pi ARM/systemd service | **Not executed in this audit**; configured CI/instructions are not results | API request to GitHub returned Forbidden |
| Docker network integration | **Not executed successfully**; prior build blocked by container Debian/proxy DNS | Native path verified without bypassing TLS/package signatures |
| Latest Render release, real meter/WAN/Wi-Fi/SD failure, live inbox, 24/7 endurance | **Not executed** | Requires operator/field/hosting validation |

The existing broader real/email browser workflows passed **10 real-mode and 7 email checks**, with zero script errors and real messages. Their final summaries are in [tests.json](audit-evidence/tests.json). Detailed local logs/screenshots are ignored by Git. Small sanitized machine-readable summaries and actual measured JSON are committed in [audit-evidence](audit-evidence/), so review does not depend on access to this cloud machine. The initial new harness omitted workers, then used an invalid forecasting interval; those harness failures were corrected by starting both supervised roles with valid intervals, without changing application checks/security or their assertions.

Nine SQLite skips correspond to concurrent email claims, concurrent link redemption, the two new authorization races, two archive races, utility configuration advisory locking, concurrent credit approval and concurrent meter duplicates. Their PostgreSQL versions all passed. Running gateway tests without `RUN_GATEWAY_E2E=1` skips the network test intentionally; that default skip must never be reported as network success.

## Requested failure scenarios: evidence and boundaries

`network` means `gateway/tests/test_end_to_end.py`, which launches a private authenticated TLS broker, verified HTTPS Flask app, dedicated PostgreSQL DB and generated credentials. `gateway unit` means `gateway/tests/test_gateway.py`. `backend` means the named tests in `tests/`.

| # | Scenario | Executed proof / result |
| --- | --- | --- |
| 1 | All three rooms | Network: every concurrent wave durably ACKed and reconciled; 174 accepted readings across three independently identified rooms. |
| 2 | One room online | Network: room1-only wave adds exactly three readings; status topic makes exactly one gateway room online, others stay offline/unknown. |
| 3 | Simultaneous rooms | Network: three separate publisher threads; backend `test_simultaneous_meter_transmissions_charge_once` separately exercises simultaneous committed requests. |
| 4 | Wi-Fi/MQTT interruption | Network: actual broker termination/restart and publisher disconnect/reconnect; persistent MQTT reconnect and original event retention pass. This models connectivity loss, not physical RF interference. |
| 5 | Internet/uplink lost | Network: HTTPS listener stopped, verified cloud request reports status 0, nine events stay in SQLite; same endpoint restored and queue drains. This is a loopback transport model of uplink loss. |
| 6 | Flask unavailable | Network: controlled 503 cloud outage held for **60.128 seconds**, with nine events continuously preserved and worker alive; retry/backoff resumes. |
| 7 | PostgreSQL unavailable | Network + `test_database_disconnect_returns_safe_503_then_recovers`: scoped SQLAlchemy OperationalError injection at the disposable app's driver boundary; health/API 503, no new commits, repaired queue drains. Shared PostgreSQL daemon is not stopped. |
| 8 | Unexpected gateway termination | Network: real gateway child process killed without shutdown after durable commits; nonzero exit observed. Does not emulate electrical power loss/SD write failure. |
| 9 | Restart with unsent data | Network: nine persisted events survive the process kill; restart recovers any sending row and delivers without duplication. Unit test also reopens journal after claim. |
| 10 | Duplicate MQTT | Network: duplicate publish and reconnect/resend produce unique local events and unique accepted readings. Gateway dedupe survives payload purging through retained receipt proofs. |
| 11 | Lost response after commit | Network: app commits 201 but transport returns 503; retry receives 409 and exact authenticated receipt, confirming one charge only. |
| 12 | Out-of-order | Gateway unit `test_delayed_sorting_and_room_isolation`, counter contradiction and 409/404 receipt tests; backend ordered-ingestion regressions. Early local arrivals sort; already committed newer data cannot justify discarding an unaccepted older event. Conflict stays held. |
| 13 | Invalid timestamps | Gateway validation tests and backend `test_measurement_precision_and_identity_fail_atomically`: naive/future/underflow timestamps reject without a partial charge. Clock-jump/holdover tests retain uploads until trusted time. |
| 14 | Invalid credentials | Network: bad broker credentials rejected; scoped old device token fails; gateway blocked auth test preserves stream and other rooms continue. |
| 15 | Impersonate another room | Network: authenticated room1 publish on room2 topic rejected by broker ACL; gateway wrong unit/device frames and backend device-mapping tests reject. No reading reassigned. |
| 16 | Missing/expired tariff coverage | Network: fixture rate expiry blocks three room heads and all nine events; zero new charges until coverage repaired and aliases resumed. Utility backend tests cover locked pending reference, validity gaps, expiry and full rollback. |
| 17 | Storage cap | Gateway unit: pending cap and mocked exhausted free space refuse commit/acceptance ACK, preserve prior unsent rows; MQTT callback disconnects without acknowledging storage success. 20,000-row benchmark validates bounded indexed claims. No physical disk-fill experiment. |
| 18 | Existing/expired token | Existing valid, rotated-old and revoked device tokens tested, including lock-time rotation race. **Time-based device-token expiry is not implemented; that case is not claimed passed.** Activation/reset tokens do expire and their single-use/expiry cases pass in the backend suite. |
| 19 | Invalid backend replies | Gateway malformed/deep/oversized/partial-body unit transports; network boolean 201 reply held/retried and repaired. Boolean/mismatched receipt identity cannot prove acceptance. |
| 20 | Safe resume | Network credential/tariff/SQL/HTTPS/malformed-response repair and forced-kill restart drain queues; gateway bounded exhaustion/resume tests retain canonical data and isolate blocked rooms. |

These are software network tests, test transports or scoped fault injections as labeled; no actual ISP failure, wireless field test or timed-out device TTL is implied. Device TTL would require a separate reviewed lifetime policy/schema and is a documented limit rather than a secretly changed API.

## Full three-room demonstration

From a reviewed checkout, with Python 3.12, a separate PostgreSQL test database and installed Mosquitto:

```bash
python -m pip install -r requirements.lock.txt -r gateway/requirements-dev.txt
python -m pip install --no-build-isolation --no-deps -e gateway
python -m pip check

# Use ONLY expendable databases ending in _test. Do not share one between concurrent suites.
export TEST_DATABASE_URL='postgresql+psycopg://test_role@localhost/dormenergy_test'
python -m pytest -q tests --junitxml=artifacts/audit/backend-final.xml
python -m pytest -q gateway/tests/test_gateway.py

RUN_GATEWAY_E2E=1 \
GATEWAY_TEST_DATABASE_URL='postgresql+psycopg://test_role@localhost/dormenergy_gateway_test' \
python -m pytest -q -s gateway/tests/test_end_to_end.py \
  --junitxml=artifacts/audit/network-final.xml
```

Supply the database role's credentials securely through the environment/managed secret configuration rather than copying real credentials into commands. Native cloud execution uses the Unix-socket test URLs recorded in `tests.json`; test role `agent` uses private development-only socket trust. No production URL belongs in these commands. The network test checks the database suffix before clearing its fixture tables, generates private ephemeral credentials/certificates and removes its own tables/processes afterward. Mock frames carry `gateway_fixture=true`; production explicitly rejects that flag.

Optional Chromium in the network run needs Playwright in `GATEWAY_BROWSER_PYTHON` and a Chromium binary in `GATEWAY_CHROMIUM_PATH`. The app interpreter and browser interpreter may differ. The gateway always verifies its test TLS certificate. The separate loopback dashboard browser uses HTTP and aborts external requests. HTTPS browser account checks trust only their temporary loopback certificate; production TLS/CSRF are not weakened.

In this cloud machine:

```bash
RUN_GATEWAY_E2E=1 RUN_GATEWAY_BROWSER=1 \
GATEWAY_BROWSER_PYTHON=/opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python \
GATEWAY_CHROMIUM_PATH=/usr/bin/chromium \
GATEWAY_TEST_DATABASE_URL='postgresql+psycopg://agent@/dormenergy_gateway_test?host=/workspace/.dormenergy-cloud/pgsocket' \
MOSQUITTO_BIN=/workspace/.dormenergy-cloud/mqtt/usr/sbin/mosquitto \
MOSQUITTO_PASSWD_BIN=/workspace/.dormenergy-cloud/mqtt/usr/bin/mosquitto_passwd \
LD_LIBRARY_PATH=/workspace/.dormenergy-cloud/mqtt/usr/lib/x86_64-linux-gnu \
.venv/bin/python -m pytest -q -s gateway/tests/test_end_to_end.py
```

For a visible manual local MQTT demonstration, follow [gateway installation](../gateway/docs/installation.md) with a **new ignored development profile**, three aliases and persistent publisher journals. Development cloud upload stays disabled; this demonstrates storage/ACKs without changing either the retained website or Render. Physical installation uses [the ESP32 protocol](../gateway/docs/esp32-protocol.md), not the mock publisher.

## Dashboard, billing, email and browser coverage

Backend files retain tests for setup/password/profile/login/logout, forced password changes/session revocation, pending accounts/invitations/reset/single-use redemption, tenant replacement/archive/restoration, unit register/edit, unit-only snapshots/forecasts/export, account/preferences, credits/notifications, source-specific meter ingestion, Decimal deductions and rate history/expiry. New regressions exercise both authorization races, every administrator write class and modified foreign-unit read parameters directly. Existing concurrent approval, duplicate ingestion and archive tests use actual PostgreSQL row locks.

The new gateway browser uses real PostgreSQL fixture data for an owner and all three tenant accounts. The secure account/utility browser verifies strict HTTPS form behavior, activation/recovery, private fragment removal, revoked sessions, read-only unverified rates and phone layout. The forecast browser trains actual XGBoost on isolated synthetic readings, renders both plots and all four recursive horizons, checks labels/uncertainty and phone width. Broader existing workflows use `audit_browser_workflows.py`, which creates fresh local test databases and starts its own web and forecasting processes; it never uses the retained service.

Email tests mock successful provider IDs, 429, 503, timeouts, partial/malformed/oversized/deep responses, retries, same idempotency key, exhausted windows, leasing/recovery, simultaneous claims, encrypted payloads, preferences, reassignment cancellation, generic recovery, expiry, single-use links and session revocation. Automated tests send **zero real emails**. [email-system.md](email-system.md) documents the separate operator-authorized live inbox procedure. Resend acceptance and inbox delivery remain different states; no actual deliverability claim is made.

## Performance and reliability measurements

`scripts/audit_benchmark.py` creates three disposable real-mode fixture accounts, 9,999 seed readings, 20 authenticated snapshots, 60 authorized ingestion calls, and a private SQLite queue. API calls use the Flask test client (full route/auth/DB behavior, no wire/TLS latency); queue claims are local journal operations, not cloud deliveries. Queue fixture origin is explicitly development-only. Tables/temporary queues are removed afterward.

```bash
python scripts/audit_benchmark.py \
  --database 'postgresql+psycopg://test_role@localhost/dormenergy_audit_benchmark_test' \
  --output artifacts/audit/benchmark-after.json
# Large queue stress uses the same script/seed/API samples:
python scripts/audit_benchmark.py \
  --database 'postgresql+psycopg://test_role@localhost/dormenergy_audit_benchmark_test' \
  --queue-rows 20000 --output artifacts/audit/benchmark-large.json
```

Before/after results and the observed dashboard tail-latency increase are retained in [the audit table](software-audit.md). Compare under an otherwise quiet fixed host for rigorous further timing work. Three-room scope and sample sizes are explicit; these measurements do not predict production load limits.

Final real-network metrics: **129.343 s** total fixture duration, **60.128 s** 503 outage, **174/174** readings eventually accepted, **171** unique charges, **0** duplicate charges/final unresolved events/unexpected worker failures, **1** intentional process kill. Completed HTTPS POST attempt median/p95 **90.923 / 112.563 ms** across 196 attempts, including failures/retries; this is not the distribution of only accepted requests. Parent-process CPU **19.401 s**, peak RSS **135.473 MiB** (excludes child broker/Chromium/killed gateway/PostgreSQL and retained services). Average parent CPU is about 15% of one CPU over this fixture duration, not a host-wide utilization guarantee. Recovery after repair took 1.508 s credential rotation, 1.707 s tariff repair, 2.009 s injected DB repair, 1.708 s HTTPS path repair, 2.112 s malformed response repair and 1.609 s restart. Those recovery clocks begin after the repair/restart action; outages and publisher setup are not included. Polling resolution is approximately 0.1 s. The exact JSON is committed.

The 20,000-row queue run left 19,900 unsent readings intact after 100 local acknowledgments, with median claim **0.038 ms**, enqueue **0.286 ms**, CPU **12.968 s**, peak RSS **130.125 MiB** for the entire benchmark. It does not claim 20,000 network uploads.

## XGBoost software evaluation

The synthetic browser/resource fixture contains 744 generated hourly intervals with three omitted observations and an occupancy increase near the later test segment. Preprocessing found 738 covered hours and did not fill gaps with zeros. The model trained using the established chronological split and source separation, with 143 next-hour test samples. Five valid recursive origins were available; 23 candidate origins were excluded for missing targets. Models and forecasts are stored/reloaded normally; no billing transaction is changed by training.

| Horizon | Endpoint model MAE kWh | Cumulative model MAE kWh | Persistence cumulative MAE | Seasonal cumulative MAE |
| --- | --- | --- | --- | --- |
| 1 h | 0.014611 | 0.014611 | 0.006565 | 0.014400 |
| 6 h | 0.092589 | 0.296570 | 0.205610 | 0.100400 |
| 12 h | 0.007192 | 0.660375 | 0.509220 | 0.191625 |
| 24 h | 0.007230 | 1.254403 | 1.018440 | 0.000000 |

Next-hour model MAE **0.053525 kWh** versus persistence **0.016529** and seasonal **0.012944**. XGBoost performs worse in this deliberately changing fixture. Zero seasonal 24-hour cumulative error is a property of this synthetic pattern, not a real accuracy result. Exact paired sample counts, RMSE, MAPE and origins are in [forecast-benchmark.json](audit-evidence/forecast-benchmark.json). Training wall time **0.354 s**, CPU **0.801 s**, peak child process RSS **126.094 MiB**, model JSON **103,815 bytes**; forecast wall time **0.022 s**. Cold imports/native initialization are included, and one-thread XGBoost settings do not imply all imported native libraries/host processes use a single thread.

Tests verify past-only features, missing/cross-hour/epoch coverage handling, chronological model selection, separate source/unit artifacts, 720-hour hardware threshold, correct metrics, recursive lag inputs, nonfinite rejection, staleness and bounded/prorated credit-duration/rate coverage. The threshold and finite synthetic backtests are software gates, not evidence of real-device forecasting accuracy, calibrated uncertainty or credit-expiration accuracy. Representative real-data studies and drift/retraining criteria remain necessary.

## Release and dependency checks

`audit_release_check.py` requires Linux, Bash/ps/native PostgreSQL utilities, two distinct local databases ending `_test`, and a reviewed checkout. It starts the real Render entrypoint with fake private environment values, confirms Gunicorn/forecast/email children without a simulator, terminates one required child and verifies nonzero supervision/restart, checks migration drift, then backs up/restores a minimal disposable financial fixture. It clears only these databases. It does not contact Render or substitute for a production backup rehearsal.

```bash
python scripts/audit_release_check.py \
  --database 'postgresql+psycopg://test_role@localhost/dormenergy_audit_benchmark_test' \
  --restore-database 'postgresql+psycopg://test_role@localhost/dormenergy_audit_restore_test' \
  --pg-bin /path/to/postgresql/17/bin --output artifacts/audit/release-check.json
python scripts/check_dependency_advisories.py --output artifacts/audit/dependency-final.json
python -m pip check
python -m compileall -q dormenergy gateway/src scripts
bash -n scripts/start-render.sh scripts/setup-cloud.sh scripts/start-cloud.sh scripts/stop-cloud.sh
git diff --check
```

The dependency checker uses default verified TLS and PyPI release metadata for exact pins, exits nonzero for listed advisories or unavailable checks, and saves evidence. It does not execute advisory examples or claim an OS/supply-chain/pentest scan. Flask 3.1.3, pytest 9.0.3, dotenv 1.2.4, cryptography 50.0.2, setuptools 83.0.0 and wheel 0.46.3 replace affected versions. `packaging` is explicitly pinned for wheel's dependency. Build pins and direct/lock runtime pins agree.

CI now configures Linux PostgreSQL migrations/tests/advisories, Linux/Windows Python 3.11/3.12 portable backend and gateway matrices, and Linux authenticated MQTT/PostgreSQL checks with a ten-minute bound and saved artifacts. Hosted results are not available here. Windows [PowerShell instructions](../gateway/docs/installation.md) and Linux [native guide](../gateway/docs/testing.md) remain separate from platform evidence. A Windows portable SQLite pass cannot establish PostgreSQL concurrency behavior.
