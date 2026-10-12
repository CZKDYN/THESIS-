# Testing and actual evidence

Software checks use disposable fixtures; their energy values, time shifts and physical-origin labels exist **only in isolated test databases**. No real meter accuracy, live token, hosted bill or physical power-control result is claimed. The website simulator stays off in network tests.

## Reproduce the required checks

From repo root with the relevant Python environment activated:

```bash
python -m pip install -r gateway/requirements-dev.txt
python -m pip install --no-build-isolation --no-deps -e gateway
python -m pytest -q gateway/tests/test_gateway.py
python -m pip check
python -m pip wheel --no-build-isolation --no-deps ./gateway -w artifacts/gateway-wheel
```

For website regression/receipt tests, install existing `requirements.lock.txt`, create an expendable PostgreSQL database `dormenergy_test`, and set `TEST_DATABASE_URL` to its actual local test connection. The suite refuses a PostgreSQL name without `_test`; that name safeguard does not substitute for operator verification that the DB is expendable.

```bash
TEST_DATABASE_URL='postgresql+psycopg://agent@/dormenergy_test?host=/workspace/.dormenergy-cloud/pgsocket' python -m pytest -q tests
```

The socket/user above describes this Codex environment; use your own isolated PostgreSQL connection elsewhere, never the website production `DATABASE_URL`.

Native network test on Linux requires Mosquitto and its password utility, existing website dependencies plus the gateway package, and a **different disposable DB** `dormenergy_gateway_test`:

```bash
RUN_GATEWAY_E2E=1 GATEWAY_TEST_DATABASE_URL='postgresql+psycopg://agent@/dormenergy_gateway_test?host=/workspace/.dormenergy-cloud/pgsocket' python -m pytest -q -s gateway/tests/test_end_to_end.py
```

Do not run two destructive test suites against the same database at once. `MOSQUITTO_BIN` and `MOSQUITTO_PASSWD_BIN` can select trusted native binaries outside PATH. On this rootless Codex machine they are extracted verified Debian packages under `/workspace/.dormenergy-cloud/mqtt/`; set `LD_LIBRARY_PATH` to their corresponding `usr/lib/x86_64-linux-gnu` directory. Normal Pi/Linux packages on PATH need no special library path.

By default the E2E test is **skipped**, with a reason; that is not a passed network test. `RUN_GATEWAY_E2E=1` requires actual broker binaries and a dedicated PostgreSQL URL and fails if prerequisites are missing. It generates private temporary credentials/CA, runs authenticated TLS MQTT and a verified HTTPS Flask server on loopback, and removes its fixture tables/processes afterward. It never sends HTTP to Render or uses `.env` credentials. The test explicitly disables mail and forecasting workers; synthetic observations cannot enter your real model.

For Windows native syntax:

```powershell
$env:RUN_GATEWAY_E2E = '1'
$env:GATEWAY_TEST_DATABASE_URL = 'postgresql+psycopg://gateway_test:isolated-test-only@127.0.0.1:5432/dormenergy_gateway_test'
$env:MOSQUITTO_BIN = 'C:\Program Files\mosquitto\mosquitto.exe'
$env:MOSQUITTO_PASSWD_BIN = 'C:\Program Files\mosquitto\mosquitto_passwd.exe'
python -m pytest -q -s gateway/tests/test_end_to_end.py
Remove-Item Env:RUN_GATEWAY_E2E
Remove-Item Env:GATEWAY_TEST_DATABASE_URL
```

These are explicit throwaway DB example credentials; create that isolated local user/database first. [Docker Compose](installation.md) creates them automatically and is the simpler Windows test route. Dockerfiles exclude `.env`, local secrets and journals and copy only test-required source. Native Python/PowerShell and Pi OS commands require validation on the actual chosen machines.

## Optional real browser rendering

The mandatory E2E check verifies actual dashboard HTTP routes and scoped snapshot data. An additional Chromium check renders the page/JavaScript for the test owner and three tenants, checks balances/unit counts/live-mode labels, switches consumption/transaction/device/forecast pages, and saves fixture screenshots. It preserves the website's CSP. It uses a second **HTTP loopback test UI server**; gateway API ingestion still uses verified HTTPS, with no TLS checks disabled.

```bash
python -m pip install -r gateway/requirements-browser.txt
python -m playwright install chromium
RUN_GATEWAY_E2E=1 RUN_GATEWAY_BROWSER=1 GATEWAY_TEST_DATABASE_URL='postgresql+psycopg://agent@/dormenergy_gateway_test?host=/workspace/.dormenergy-cloud/pgsocket' python -m pytest -q -s gateway/tests/test_end_to_end.py
```

Use `GATEWAY_CHROMIUM_PATH` for a system Chromium and `GATEWAY_BROWSER_PYTHON` for a separate interpreter already containing Playwright. Credential inputs are generated for disposable accounts and sent to the browser helper on private stdin, not argv/logs. The helper blocks non-loopback requests and redacts fixture secrets from failure diagnostics.

## Coverage

Unit tests check identity and precision/range/NaN/JSON/time guards; real-fixture prohibition and config/TLS/origin restrictions; durable restart, exact duplicates/content conflicts, dedupe/counter checkpoints after payload retention; late arrival ordering and separate room heads; reset approval and epoch reuse; storage admission/free-space failures with no acceptance ACK; consistent backup/integrity; stale status versus old readings; finite retries and HTTP authentication/validation/tariff/conflict states; 409 exact receipt versus missing/mismatched data; NTP startup/holdover/jump stabilization; single-worker locks; and fatal callback/uploader failure without secret logging.

Network test checks **three independent publishers**, authenticated TLS broker rejection and ACL cross-room rejection, local application ACKs before an unavailable cloud accepts anything, ordered HTTPS delivery to PostgreSQL, disconnect/reconnect, gateway and broker restart, acceptance followed by a lost response and exact duplicate receipt recovery, 27 readings/24 independent interval charges, and owner/three-tenant dashboard data isolation. A deliberately untrusted TLS certificate is refused. Website tests separately confirm scoped receipt auth, archived-unit rejection, unchanged balances on duplicate/conflict/tariff failure, and production fixture rejection.

CI adds gateway unit jobs for Linux/Windows on Python 3.11/3.12 and a Linux MQTT/PostgreSQL job. They are configured, not claimed as executed until GitHub reports their result.

## Platforms and hardware limits

This cloud machine is Linux x86_64/Python 3.12/PostgreSQL 17/Mosquitto 2.0.21. Mock samples are not PZEM observations. Pi ARM runtime, actual Windows commands/ACLs, ESP32 firmware/storage wear, UART scaling/calibration, NTP drift during real outages, abrupt power loss/SD durability, multi-floor Wi-Fi latency/loss and occupied-building electrical safety need hardware trials. The gateway itself does not train a synthetic production model or switch mains. Rootless Mosquitto test brokers and ephemeral test CA keys are local fixtures; production uses the separate system service user and protected reviewed certificates.

The first combined test invocation exposed a helper-module import collision and was corrected by a distinct gateway test package. Initial Docker attempts hit a read-only home/build cache and container DNS failures; verified native tests remain independent of Docker. Browser checks initially used string-evaluation waiting blocked by the real site's CSP; the test uses locator expectations instead of weakening that policy. See the final execution record below for pass/failure counts and any unresolved environment limitations.

## Final execution record — 2026-10-12

| Executed check | Actual result |
| --- | --- |
| Website regression, PostgreSQL 17 | **87 passed**, zero skipped; includes five new receipt/auth/fixture/duplicate/deep-JSON checks. |
| Gateway unit tests, local SQLite | **59 passed**, zero skipped. Latest rerun includes deep-JSON rejection through the MQTT callback and private backup-file creation. |
| Full native gateway run including required network E2E | **60 passed**, zero skipped (59 unit + 1 E2E). Three rooms produced 27 readings and 24 isolated charges; outage buffering, gateway/broker restart, exact duplicate receipt, auth/ACL/TLS rejection passed. |
| Optional actual Chromium inside that E2E | Owner and all three tenant dashboards rendered correct balances/unit counts and opened consumption/transaction/device/forecast views with **zero script errors**. Four fixture screenshots saved. |
| Actual CLI development workflow | Private setup → authenticated broker → gateway CLI → three duplicate publishers → health → backup/restore-check passed: **9 unique durable unsent rows, zero cloud upload**, integrity `ok`. |
| Packaging / dependencies | Editable installation and wheel build passed; `pip check` found no broken requirements. Wheel: `artifacts/gateway-wheel/dormenergy_gateway-0.1.0-py3-none-any.whl`. |
| Setup/helper validation | Saved install instructions executed successfully with retained `.env`/DB preserved; Python compilation, shell syntax, Compose config parsing and Git whitespace checks passed. |
| systemd unit verification | Syntax verified with the executable/working directory substituted to this local installation; actual Pi user/service startup remains untested. |
| Optional Docker build/run | **Build blocked**: container DNS could not resolve `deb.debian.org`, then the provided `proxy`, including a host-network attempt. No container network test passed; native E2E passed independently. TLS/signature verification remained enabled. |
| Windows / Raspberry Pi / GitHub CI | Instructions and CI jobs added; **not executed on those platforms**. No physical sensors, mains installation, firmware or field trial tested. |

Expected ERROR/WARNING log lines in passing tests are deliberate injected storage/worker/broker failures; original readings remain durable. Earlier browser waiting and shared-process migration logging test failures were corrected and rerun successfully. The Docker DNS limitation remains an environment limitation, not an unreported passing test. Temporary native test services were stopped and the E2E fixture tables removed; retained website/worker processes and production source branch were preserved.
