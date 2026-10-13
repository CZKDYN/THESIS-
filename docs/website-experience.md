# Website experience implementation and validation

Reviewed 14 October 2026, Asia/Manila. Branch: `feature/website-experience`, based on stabilization `d7527b48080cadbff36db7b221bb72eb6ed40202`. Integration target: `feature/software-stabilization`. Main remains `24f6ecf4ff565d7f9ea538c163a5ae8a47d62152`.

This extends the existing Flask/Jinja/JavaScript/locally bundled Chart.js implementation. Billing, account authorization, email encryption/outbox, XGBoost training/inference and the gateway package retain their implementations. Changes are read-only reporting and interface improvements; existing approval/management forms still call their protected backend workflows.

## Audit and before/after

The [baseline recorded before editing](website-experience-baseline.md) inventories existing functionality and problems. Existing registration, unit archive/restore, tenant assignment/invitations, forced password changes, device-token management, manual load approval, protected rates, CSV export and forecasting were reused. Authentication/account/link templates, API/models, audit, forecasting, testing and deployment documents were inspected. The unchanged real-mode browser workflow passed 10 checks before edits and supplied the before screenshots.

| Before | Implemented result |
| --- | --- |
| Fixed PHP1,000 balance bar | Actual PHP credit, configured warning threshold and latest recorded top-up; no battery/percentage metaphor |
| Pending count from latest 100 requests | SQL count over the entire authorized pending scope |
| Historical alerts mixed with work | Current tasks derived from current accounts, requests, receipts, rates and model age; notification history remains separate |
| Latest 40 transactions/100 requests without navigation | Filtered server pages of 25, complete matching counts and bounded stable-insert paging |
| Receipt timestamps described as Pi health | Original measurement and backend receipt times displayed separately; broker/queue/Pi health explicitly unavailable |
| Missing numeric values could display zero | Unknown values display an em dash; chart gaps remain null, observed zero remains zero |
| Dense forecast comparison prose | Next-hour and recursive tables, matched sample counts, MAPE limits and explicit baseline underperformance warning |
| Tiny labels, weak contrast, focus replacement | Readable refinements, labeled dialogs, chart summaries/data tables, native focus behavior and deferred background DOM replacement |
| In-flight updates could race with unit changes | Serialized, coalesced snapshots that discard older-filter replies; retained filters/forms and retry banner |

The first actual axe scan found contrast failures, and the expanded scan found two additional mobile contrast failures. Both were fixed and rerun; the final 15 scans have zero reported violations. An existing tenant-assignment browser check also exposed a deferred table update after successful submission. Explicit successful submissions now focus the page heading before refreshing, while background updates continue preserving active inputs. Assertions were retained.

## Administrator and tenant experience

Administrators receive balance, observed daily energy, fresh current power, reporting-meter counts, recent/no-recent receipt counts, active units, stale samples, low balances, complete pending totals and activation counts. The unit selector scopes operational summaries, charts and histories. Current tasks link to existing loads, units, devices, notifications, rate and forecast views. Failed email tasks count recent failures for eligible current accounts, not all historical failures. Retraining tasks use stored model age/source and do not run inference or enqueue training during refresh.

Room cards show unit/room/tenant, actual credit, warning threshold, observed daily kWh, fresh-only W, original measurement time and textual freshness. A labeled native detail dialog shows available information and can select the unit. Existing archive/restore, tenancy, token and invitation controls remain available to administrators.

Tenants see their available balance first, threshold-based warning, latest recorded approved credit and existing request shortcut. Daily, seven-day and 30-day usage comes from backend observations; expenses come from negative consumption ledger amounts, without multiplying history by today's rate. Periods are Manila calendar days including today and may be partial. kWh, W, additions and deductions have distinct labels. A request remains pending until manual verification and protected approval; submitting it does not change credit.

The optional previous-period percentage is withheld because complete comparable coverage is not established. No appliance attribution is inferred. Notification categories filter the existing latest 12 scoped messages; there is no invented unread count or persistent acknowledgment state.

## Consumption, device evidence and forecasting

Existing 24-hour, seven-day and 30-day charts retain missing-data gaps. Tooltips use original Manila bucket dates/times and observed-unit counts, and consumption charts have text summaries and keyboard-accessible data tables. Totals may include only some selected units; they are not represented as complete dormitory coverage. The backend excludes future and mismatched-source observations from usage aggregates, and latest displayed measurements must match the configured device source. Synthetic fixtures retain explicit source labels.

Device monitoring separates fresh/stale/awaiting/clock-ahead samples from recent backend receipt evidence. Current power requires both a fresh sample and recent receipt; paused backend updates suppress live power and mark status unconfirmed while retaining recorded history. Voltage/current/counter values are labeled last recorded. Supply remains the reported state, including unknown. Token provisioning is shown without claiming upstream authentication health. No gateway-health API, relay switch, cutoff or trip override is added.

Forecasting presents next-hour consumption, generation/horizon, supported estimated credit duration, training date, configured model age/retraining state and source. Expired models or unsupported points do not appear as current predictions. The existing next-hour MAE/RMSE/MAPE/sample counts, persistence/seasonal baselines, historical comparison and 1/6/12/24-hour recursive endpoint/cumulative metrics are organized into tables. A fair same-sample persistence comparison produces an explicit caution when XGBoost does not outperform it. Different seasonal sample counts are disclosed. Estimates, occupancy changes, missing data, training coverage and absence of calibrated confidence intervals remain explicit; learning algorithms are unchanged.

## API, database and performance

The [API contract](api.md#website-reporting-additions) documents additive snapshot fields and new session-protected GET `/api/transactions` and `/api/load-requests`. Shared scoped queries implement literal search, Manila dates, deterministic descending order and bounded pages. Tenant history/request access retains the existing tenancy/user policy; foreign unit requests fail. Requested amount, actual ledger credit and review time are separate.

Paging anchors prevent new inserts from moving deeper pages but are not a frozen snapshot of mutable request statuses. CSV uses the same filters across pages with fresh data, exact decimals, existing formula escaping and the existing latest-10,000 limit. Its seven-column UTC contract is preserved, including administrator archived/demo exports. Invalid dates disable the interface export link and also fail server validation.

There are no new models, migrations, indexes, production dependencies or workers. Schema head remains `d21ae48f397c`, with 17 tables. The local schema drift and disposable backup/restore check passed. Persistent notification read states would require a separately justified migration.

The three-unit/9,999-reading benchmark executes 16 dashboard SQL queries, median 28.664 ms, p95 60.614 ms over 20 requests. The earlier stabilization record measured 11 queries, median 12.499 ms, p95 25.017 ms. These runs occurred at different times; the new run overlapped other local tests, so timings are indicative rather than a controlled latency comparison. Five batched queries buy complete pending counts, exact ledger period totals/latest credit, source-specific model ages and recent failed delivery counts. Monthly usage stays database-aggregated rather than loading readings into Python. This is local software evidence, not hosted capacity or endurance evidence.

Snapshots are completion-scheduled every 10 seconds, paused while the page is hidden, and back off to 60 seconds after repeated failures. Snapshot requests do not overlap, and queued filter changes use the newest selection. Reports independently serialize requests and discard outdated filters. Fetches time out at 15 seconds; writes are never automatically replayed. On an unconfirmed write, users are asked to check recorded history before retrying. Background renders preserve focused controls, open dialogs and unsaved form values; successful explicit actions can update the view.

## Browser screenshots

Actual Chromium captures, not design mockups: [before/after gallery and fixture boundaries](website-evidence/README.md). Committed examples include desktop operations, 360px administration/history, 390px tenant overview, forecasting and device monitoring. Local ignored `artifacts/website/` additionally contains all requested widths: 360, 390, 768, 1024 and 1440px. Before and after use different disposable fixtures; their monetary/energy values are not a before/after performance or consumption comparison.

## Executed validation

All mutating checks used disposable local `_test` databases or temporary SQLite, generated credentials and loopback services. No live production reading or real email was sent. Linux x86_64, Python 3.12.14, PostgreSQL 17, Chromium and Playwright 1.62.0 were executed. Test-only browser pins are separate from Render requirements.

| Check | Final result |
| --- | --- |
| Full PostgreSQL backend | 145 passed, 0 failures/errors/skips, 87.81 s |
| Portable SQLite backend | 136 passed, 9 explicit PostgreSQL-only skips, 0 failures/errors, 77.41 s |
| Gateway unit | 68 passed, 0 failures/skips |
| Authenticated MQTT/TLS/HTTPS/PostgreSQL integration | 1 passed; final evidence includes owner/three tenant Chromium dashboards |
| Existing real-mode browser | 10 checks passed |
| Memory-email activation/recovery browser | 7 checks passed; 0 real messages |
| Strict HTTPS/cookie/CSRF/account/rate browser | 8 checks passed |
| Actual XGBoost synthetic forecast browser | 4 checks passed |
| New website workflow browser | 13 grouped checks passed, no page JavaScript errors |
| axe-core 4.14.0 | 15 page/dialog states, 0 reported A/AA-tag violations |
| Schema, local Render supervision/restart, disposable backup/restore | Passed; fixture money/table counts preserved |
| Exact Python dependency release advisories | All active pins checked, no listed advisories; includes browser-only pins |
| Python dependency consistency, JS syntax, Git whitespace | Passed |

The new backend cases verify complete counts beyond 100, measured-vs-received times, exact expenses despite a different current tariff, missing versus observed zero/future/wrong source, concurrent inserts during paging, Manila date boundaries and CSV agreement, actual credited/reviewed details, invalid filters, literal wildcard/SQL-looking search and tenant isolation. The new browser groups additionally verify filters, approval, unsaved dialogs/native validation, 503/malformed response recovery, snapshot filter races, expired forecast display, all five widths, drawer focus/Escape/inert, synthetic labels and pending requests not crediting balances. Mobile history scrolls horizontally without hiding essential financial columns.

The 15 automated accessibility states are login; admin overview, transactions, loads, forecast, devices, edit dialog, mobile navigation and consumption; tenant overview, load dialog, notifications, account, recovery and invalid activation. CSP remains enabled: axe is checksum-verified and supplied only by a test browser route. Automated checks and exercised keyboard paths do not constitute a complete WCAG certification. Screen-reader speech, all assistive technologies, 200/400% zoom and every possible content length were not manually evaluated.

Reproduction (working directory is the existing checkout; no worktree is needed):

```bash
# Use distinct expendable databases; never run destructive suites on one DB together.
TEST_DATABASE_URL='postgresql+psycopg://agent@/dormenergy_test?host=/workspace/.dormenergy-cloud/pgsocket' \
  .venv/bin/python -m pytest -q tests --junitxml=artifacts/website/backend.xml
.venv/bin/python -m pytest -q tests --junitxml=artifacts/website/portable.xml
.venv/bin/python -m pytest -q gateway/tests/test_gateway.py --junitxml=artifacts/website/gateway.xml

# Native cloud broker paths; standard installations use their installed equivalents.
RUN_GATEWAY_E2E=1 RUN_GATEWAY_BROWSER=1 \
  GATEWAY_BROWSER_PYTHON=/workspace/THESIS-/.venv/bin/python GATEWAY_CHROMIUM_PATH=/usr/bin/chromium \
  GATEWAY_TEST_DATABASE_URL='postgresql+psycopg://agent@/dormenergy_gateway_test?host=/workspace/.dormenergy-cloud/pgsocket' \
  MOSQUITTO_BIN=/workspace/.dormenergy-cloud/mqtt/usr/sbin/mosquitto \
  MOSQUITTO_PASSWD_BIN=/workspace/.dormenergy-cloud/mqtt/usr/bin/mosquitto_passwd \
  LD_LIBRARY_PATH=/workspace/.dormenergy-cloud/mqtt/usr/lib/x86_64-linux-gnu \
  .venv/bin/python -m pytest -q -s gateway/tests/test_end_to_end.py --junitxml=artifacts/website/network.xml

.venv/bin/python -m pip install -r requirements-browser.txt
.venv/bin/python scripts/fetch_accessibility_checker.py
AXE_CORE_PATH=artifacts/website/axe.min.js .venv/bin/python scripts/browser_experience_smoke.py
.venv/bin/python scripts/audit_browser_workflows.py
.venv/bin/python scripts/browser_https_smoke.py
.venv/bin/python scripts/browser_forecast_smoke.py
.venv/bin/python scripts/check_dependency_advisories.py --output artifacts/website/dependency-final.json
.venv/bin/python -m pip check
node --check dormenergy/static/js/dashboard.js
git diff --check

.venv/bin/python scripts/audit_benchmark.py \
  --database 'postgresql+psycopg://agent@/dormenergy_audit_benchmark_test?host=/workspace/.dormenergy-cloud/pgsocket' \
  --output artifacts/website/performance-after.json
.venv/bin/python scripts/audit_release_check.py \
  --database 'postgresql+psycopg://agent@/dormenergy_test?host=/workspace/.dormenergy-cloud/pgsocket' \
  --restore-database 'postgresql+psycopg://agent@/dormenergy_audit_restore_test?host=/workspace/.dormenergy-cloud/pgsocket' \
  --pg-bin /workspace/.dormenergy-cloud/postgres/usr/lib/postgresql/17/bin \
  --output artifacts/website/release-check.json
```

Portable runs must leave `TEST_DATABASE_URL` unset. The displayed cloud socket path is local tooling, not a public preview link. Native Chromium was already installed; a new browser runner elsewhere needs Chromium or `python -m playwright install --with-deps chromium`. The new Linux CI browser job installs it and uploads results/screenshots; hosted Actions execution is not yet verified. Python advisories do not constitute an OS/npm/penetration audit.

## Complete modified-file inventory

| Path | Purpose |
| --- | --- |
| `dormenergy/api.py` | Compatible read-only summaries, source-correct measurements, bounded history APIs and filter-consistent export |
| `dormenergy/services/reporting.py` | Shared authorized search/date/page query construction |
| `dormenergy/static/js/dashboard.js` | Evidence-based rendering, exact decimal presentation, charts, filters, resilient polling and keyboard behavior |
| `dormenergy/static/css/experience.css` | Readable responsive refinements, contrast, focus, reduced motion and scrolling |
| `dormenergy/templates/base.html` | Load refinements and skip link |
| `dormenergy/templates/dashboard.html` | Existing views with summaries, filters, chart text and detail dialog |
| `dormenergy/templates/dashboard_insights.html` | Operations strip and tenant threshold/usage/ledger context |
| `dormenergy/templates/history_filters.html` | Reusable labeled filter and pager markup |
| `dormenergy/templates/forecast_evaluation.html` | Baseline and recursive evaluation tables with limitations |
| `dormenergy/templates/management_dialogs.html` | Accessible names for existing management dialogs |
| `dormenergy/templates/login.html` | Keyboard skip-link destination |
| `dormenergy/templates/account.html` | Keyboard skip-link destination |
| `dormenergy/templates/email_account.html` | Keyboard skip-link destination |
| `tests/test_website_reporting.py` | 18 reporting/privacy/data-accuracy regression cases |
| `scripts/browser_experience_smoke.py` | Temporary real-rendered fixture, UX/keyboard/network assertions, screenshots and optional axe checks |
| `scripts/fetch_accessibility_checker.py` | Test-only HTTPS download with fixed SHA512 verification |
| `requirements-browser.txt` | Pinned development/CI browser dependencies; no production lock change |
| `scripts/check_dependency_advisories.py` | Include browser pins in the existing release-advisory check |
| `.github/workflows/build.yml` | Integration-target PR trigger and Linux browser/accessibility job |
| `README.md` | Link to the review deliverables |
| `docs/api.md` | Reporting/pagination/scope contract |
| `docs/testing.md` | Current validation link and safe browser reproduction |
| `docs/website-experience-baseline.md` | Audit captured before editing |
| `docs/website-experience.md` | Implementation, comparison, inventory, commands, limits and release procedure |
| `docs/website-experience-pr.md` | Concrete PR description targeting stabilization |
| `docs/website-evidence/README.md` | Evidence/screenshot index and fixture context |
| `docs/website-evidence/tests.json`, `accessibility.json`, `dependency-final.json`, `performance-before.json`, `performance-after.json`, `network.json`, `release-check.json` | Actual sanitized test, scan, advisory, performance, network and release evidence |
| `docs/website-evidence/before-admin-desktop.png`, `before-tenant-mobile.png`, `before-units-mobile.png`, `before-empty-workspace.png` | Four unchanged-interface captures |
| `docs/website-evidence/admin-overview-1440.png`, `admin-overview-360.png`, `tenant-overview-390.png`, `admin-history-360.png`, `forecast-1440.png`, `devices-1440.png` | Six final rendered captures |

The legacy `app.css` is retained and refined by the later stylesheet. Models, migrations, gateway source, training algorithms, billing services, production dependency files, Render configuration and production secrets are unchanged.

## Limits and controlled deployment checklist

Actual Pi/ESP32/PZEM connectivity, calibrated consumption, real-data forecast quality, tariff applicability, real email inbox delivery, hosted capacity, hosted Actions and fresh cloud snapshot restoration are not established by these local tests. No physical control is introduced. Complete comparable-period trends, persistent unread state and authoritative gateway health are deliberately absent. Large-volume substring filtering/offset pagination can need indexing or keyset paging beyond the three-room scope; no general scale claim is made.

1. Review this branch against `feature/software-stabilization`, including both branches' independent changes and evidence. GitHub API access returned Forbidden here; use the [prepared comparison/PR form](https://github.com/CZKDYN/THESIS-/compare/feature/software-stabilization...feature/website-experience?expand=1) and [PR body](website-experience-pr.md). A pushed branch is not an opened PR.
2. Run hosted checks and manually review keyboard, zoom and screen-reader behavior. The CI configuration is source-reviewed/local-workflow validated, not a recorded hosted pass.
3. On an authorized staging instance, retain real-mode operation, disabled email until configured, and unconfirmed utility rates until valid owner evidence exists. Preserve encryption keys, device credentials, ledger records and queues. This change needs no new environment secrets or migration.
4. Capture the current release SHA and database/queue backup with a tested restoration procedure. Apply the existing schema migrations only if staging is behind; confirm `flask db check` and normal workers/health/login.
5. Verify empty/fresh/stale units, each role, request approval, filters/export, account links and forecast limits using authorized staging data. Never inject the screenshot fixture into production.
6. Only after explicit operator approval, integrate/release through the normal procedure and select the reviewed commit in Render. This task does not merge or deploy automatically.
7. After release, watch response/errors, database queries and worker exits. If needed, revert to the recorded prior code SHA; do not rewrite balances, history or schema to undo a presentation change. Follow the existing [release/rollback guide](release-readiness.md).
