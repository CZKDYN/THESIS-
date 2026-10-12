# DormEnergy Raspberry Pi gateway

Package 0.1.1 is part of the unmerged `feature/software-stabilization` review. The original gateway scope and evidence below are supplemented by [the integrated audit](../docs/software-audit.md), [fresh validation](../docs/software-validation.md) and [controlled release checklist](../docs/release-readiness.md).

A headless, monitoring-only **MQTT → durable SQLite → authenticated HTTPS** gateway for independent ESP32/PZEM-004T meters. Default development mode collects explicitly labeled local fixtures and **cannot upload**. Actual installation uses explicit real mode, verified TLS, room-specific broker credentials/ACLs and the website's independently scoped device tokens. Python 3.11+ (tested with 3.12).

This is gateway software, not ESP32 firmware or an assembled electrical installation. The firmware must implement [the protocol](docs/esp32-protocol.md), including a durable upstream journal and an application ACK. No relay, breaker, mains GPIO, or automatic cutoff is implemented.

## Start with software tests

From the repository root on Linux/macOS:

```bash
python3 -m venv gateway/.venv
source gateway/.venv/bin/activate
python -m pip install -r gateway/requirements-dev.txt
python -m pip install --no-build-isolation --no-deps -e gateway
bash gateway/scripts/run-tests.sh
```

Windows PowerShell:

```powershell
py -3.12 -m venv gateway/.venv
.\gateway\.venv\Scripts\Activate.ps1
python -m pip install -r gateway/requirements-dev.txt
python -m pip install --no-build-isolation --no-deps -e gateway
.\gateway\scripts\run-tests.ps1
```

If activation is restricted, use `gateway/.venv/Scripts/python.exe` directly instead of changing your execution policy. Unit tests require neither hardware nor cloud credentials. The [installation guide](docs/installation.md) covers a visible three-room local MQTT demonstration, isolated PostgreSQL/dashboard tests, and Pi deployment.

For the complete isolated network test with Docker Desktop (Linux containers), from repo root:

```powershell
docker compose -f gateway/compose.test.yml up --build --abort-on-container-exit --exit-code-from tests
$testExit = $LASTEXITCODE
docker compose -f gateway/compose.test.yml down --volumes
if ($testExit -ne 0) { throw "Gateway tests failed with exit code $testExit" }
```

The runner generates temporary test credentials and TLS certificates, launches local MQTT/Flask, uses an expendable PostgreSQL database, and checks admin/tenant dashboard responses. It never mounts your `.env`, reaches Render, or enables the website simulator. Linux native execution was verified; Docker Desktop and Windows execution still require validation on those platforms.

## What is implemented

- QoS 1 MQTT subscription with per-device exact topic/identity validation; TLS server verification and documented broker ACLs.
- Decimal/range/precision checks, stable event ID and meter epoch, original UTC sampling timestamp, sender clock flag; real-mode NTP/clock-jump upload holds.
- SQLite WAL with FULL commits, per-device timestamp order, exact-content deduplication, crash recovery, bounded storage admission and retry budgets. Unsent rows are retained.
- Application ACK **after** local commit. MQTT PUBACK is transport acknowledgment, not evidence of durable application storage.
- Existing bearer API ingestion; 201 acceptance checks and scoped read-only receipt comparison on every 409. Authentication, ordering, missing tariff and exhausted retries hold the affected stream for operator review.
- Broker/runtime, room freshness/status, queue/error, last synchronization, disk and cloud-path diagnostics. Internet reachability is reported only to the tested cloud path; the tool does not guess why a transport request failed.
- Secret provisioning without command-line values, consistent backup/restore checks, single-worker locks, structured redacted logs, and a least-privilege systemd service.
- Explicit mock publisher: three independent room profiles, duplicate, delayed, out-of-order, disconnect/reconnect, persistent retry journal, and operator-approved counter reset scenarios.

No schema migration was added. Website changes are limited to stable device error codes, a fixture guard and an authenticated read-only receipt endpoint. Accounts, tariffs, billing calculations, email and XGBoost behavior remain in the cloud application. Merge/deploy the reviewed backend receipt addition before activating a real gateway; an older backend leaves ambiguous duplicates held safely.

## Documentation

| Guide | Contents |
| --- | --- |
| [Architecture](docs/architecture.md) | Complete file tree/purposes, isolation, failure states, storage and security boundaries. |
| [Installation](docs/installation.md) | Exact Windows, isolated test and Raspberry Pi commands; TLS, Mosquitto and systemd. |
| [Configuration](docs/configuration.md) | Modes, credentials, storage limits, NTP, token rotation, recovery/backup/update commands. |
| [ESP32 protocol](docs/esp32-protocol.md) | Payload, application ACK, status/LWT, clock, counter/reset and future control contract. |
| [Testing](docs/testing.md) | Executed results, reproducible checks and platform/hardware limits. |
| [Troubleshooting](docs/troubleshooting.md) | Failure diagnosis and chronological physical integration checklist. |

Production remains `SIMULATION_MODE=false`. Real tokens never belong in Git, screenshots, test fixtures, publisher journals or command-line arguments. Private local development files go in ignored `gateway-local/`; Pi secrets stay under `/etc/dormenergy-gateway/secrets/`.
