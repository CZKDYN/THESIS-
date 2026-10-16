# Dashboard refinement and Meralco reference fix

16 October 2026, Asia/Manila. Branch: `feature/dashboard-refinement`, based on `feature/website-experience` at `754783f844f9e4f35cb44e97c4153b4ea9a8cbf7`. Remote branch inspection confirmed that was the latest development head before editing. The initial worktree was clean. This is a targeted extension of the existing Flask/Jinja/CSS/JavaScript interface.

## Implemented refinements

- Removed the workspace identity card (`.workspace-label` / `.workspace-avatar`) and thesis promotional card (`.prototype-note`) from the HTML, rather than hiding them. Removed all 15 associated CSS rules, the unused old navigation-heading rules, and the remaining shared-selector references.
- Organized the same administrator/tenant views into Monitoring, Billing and Account groups. Existing view IDs, form endpoints, role restrictions and navigation handlers remain intact. No hard-coded property label replaces the university branding; the application has no property-name field to source one from.
- Kept the authenticated name, account role/unit and CSRF-protected logout at the bottom of the sidebar. Navigation scrolls independently on short screens, keeping those controls visible. Refined logo spacing, icon sizes, hover/active/focus styling and profile wrapping. Tablet-specific logo sizing prevents icon compression and keeps the tagline on one line.
- Refined card borders, rounding and shadows within the existing navy/white/emerald design. Flattened the forecast tile's gradient and changed promotional tile wording to a clear functional title. Tables, statistics, chart data and existing empty/loading/error states retain their behavior.
- Replaced the academic dashboard footer with a product footer and a keyboard-operable native “System information” disclosure. It explicitly identifies the development build and explains that meter integration, tariff applicability and forecast quality require deployment-specific validation. The simulation badge and synthetic-data labels remain visible. Real mode is labeled “Hardware mode” instead of “Live workspace”; this describes configuration, not a production-readiness certification.
- Removed university/department wording from the sign-in story footer while retaining a development-build disclosure. The account and authentication flows are unchanged.

## Replacement Meralco URL and verification

**Selected URL:** https://company.meralco.com.ph/news-and-advisories/higher-residential-rates-october-2026

The owner confirmed on 16 October 2026: **“It opens the October 2026 rate advisory.”** The alternate Summary Schedule candidate (`https://company.meralco.com.ph/node/8985`) also showed an error in the owner's browser, so it was rejected.

Automated HTTPS requests to both supplied candidates were blocked by this environment's proxy: CONNECT 403, curl exit 56, no Meralco origin HTTP response. Consequently the access verification is the owner's browser confirmation, not an independent server fetch or automated content/title extraction. [Machine-readable verification record](dashboard-refinement-evidence/meralco-reference.json).

The dashboard uses the official HTTPS `company.meralco.com.ph` destination, “View official Meralco rate information,” `target="_blank"`, `rel="noopener noreferrer"`, an external-link icon and a screen-reader new-tab hint. It identifies the resource as an **October 2026 residential-rate advisory** and asks the operator to confirm the applicable tariff and period using the actual utility bill. Both old archive URL occurrences (template and utility-rate documentation) were replaced; no old URL remains in tracked source/docs/tests.

The PHP14.74/kWh reference remains read-only and unverified. No published news value is imported, no tariff is confirmed or activated, and no pricing calculation, period, account balance or historical charge is changed. The [utility safeguards and verification record](utility-rates.md#meralco-reference-availability) remain authoritative. This monthly reference can become dated; future reference updates need review rather than automatic rate fetching.

## Actual validation

All application/browser mutations used disposable local test databases and clearly identified fixtures. No production readings, real emails or hosted billing changes were made. Execution: Linux x86_64, Python 3.12.14, PostgreSQL 17, Chromium 154 and Playwright 1.62.0. The existing cloud dependencies and services supported the workflow; no installation/startup configuration change was needed.

| Check | Executed outcome |
| --- | --- |
| Complete PostgreSQL backend suite | 145 passed; 0 failed/errors/skipped; 93.393 seconds |
| Gateway unit suite | 68 passed; 0 failed/errors/skipped; 0.218 seconds |
| Existing real-mode browser | 10 grouped checks passed |
| Memory-email account/invitation/recovery browser | 7 grouped checks passed; no real messages |
| Strict HTTPS/cookie/CSRF/account/rate browser | 8 grouped checks passed |
| Actual XGBoost synthetic forecast browser | 4 grouped checks passed |
| Refined interface browser | 17 grouped checks passed; no page JavaScript errors |
| axe-core 4.14.0 | 18 page/dialog states, zero reported A/AA-tag violations |
| JavaScript/Python syntax, CSS brace balance, removed-component scan, Git whitespace | Passed |

The **46 grouped browser checks** cover administrator and tenant workflows, exact existing financial/privacy behavior, forecasts, focused/unsaved forms, native/server validation, 503/malformed reply recovery and unit-filter request races. Refinement-specific assertions verify both removed components are absent from the DOM, grouped navigation, real profile information, development disclosure, actual logout/session loss for both roles, the selected link markup and unchanged locked reference/no active tariff.

Actual layout captures/checks cover 360, 390, 768, 1024 and 1440px widths. Additional 600px-high desktop/tablet/phone checks confirm independently scrolling navigation and visible profile/logout controls. The mobile drawer's focus, Escape, inert background and active states remain exercised. Official-link labeling/attributes and narrow rate-page layout are checked without falsely reporting an automated external page visit.

The 18 automated accessibility states include login, overview, transactions, loads, forecasts, devices, an edit dialog, mobile navigation/consumption, tenant overview/request/notifications/account/recovery/invalid activation, plus system information and desktop/mobile electricity rates. CSP stays enabled; the verified axe script is supplied only through the browser's test route. Automated scans and exercised keyboard paths are partial evidence, not a full WCAG certification or manual screen-reader evaluation.

Executed commands from the existing `/workspace/THESIS-` checkout:

```bash
# Dedicated expendable test database; never substitute retained/production data.
TEST_DATABASE_URL='postgresql+psycopg://agent@/dormenergy_test?host=/workspace/.dormenergy-cloud/pgsocket' \
  .venv/bin/python -m pytest -q tests --junitxml=artifacts/dashboard-refinement/backend.xml
.venv/bin/python -m pytest -q gateway/tests/test_gateway.py \
  --junitxml=artifacts/dashboard-refinement/gateway.xml

EXPERIENCE_ARTIFACT_DIR=artifacts/dashboard-refinement \
  AXE_CORE_PATH=artifacts/website/axe.min.js \
  .venv/bin/python scripts/browser_experience_smoke.py
.venv/bin/python scripts/audit_browser_workflows.py
.venv/bin/python scripts/browser_https_smoke.py
.venv/bin/python scripts/browser_forecast_smoke.py
node --check dormenergy/static/js/dashboard.js
git diff --check
```

The browser tools/pinned axe were already installed from the preceding website task; [browser setup instructions](testing.md#website-experience-validation--14-october-2026-asiamanila) still apply. Raw logs/JUnit and all-width captures are ignored local artifacts; [committed results and screenshots](dashboard-refinement-evidence/README.md) are available for review. No new unit tests that merely mirror CSS were added. The existing real-mode browser's exact mode-caption assertion was updated to the intentional “Hardware mode” label while retaining its independent backend mode, account and billing assertions.

## Modified files

| File | Purpose |
| --- | --- |
| `dormenergy/templates/dashboard.html` | Remove both sidebar cards, group existing navigation, retain profile/logout, professional footer/system information and confirmed official advisory link |
| `dormenergy/templates/base.html` | Shared decorative external-link SVG symbol |
| `dormenergy/templates/login.html` | Replace academic footer wording with product/development context |
| `dormenergy/static/css/app.css` | Delete obsolete component/navigation rules and tiny forced logo typography |
| `dormenergy/static/css/experience.css` | Sidebar spacing, independent scroll, profile wrapping, active/focus styles, consistent cards and responsive footer disclosure |
| `dormenergy/static/js/dashboard.js` | Accurate mode caption and system-information data-mode text only |
| `scripts/browser_experience_smoke.py` | Extend actual rendered regression/accessibility/screenshot checks; optional artifact output directory |
| `scripts/browser_real_smoke.py` | Match the intentional hardware-mode caption without weakening the real-mode assertions |
| `.github/workflows/build.yml` | Run existing PR jobs for the website-experience integration target as well |
| `docs/utility-rates.md` | Replace obsolete link and record actual user verification/automated fetch limitation |
| `README.md` | Link the refinement deliverables |
| `docs/dashboard-refinement.md` | Implementation, verification, commands, inventory and release notes |
| `docs/dashboard-refinement-evidence/README.md` | Screenshot and evidence index |
| `docs/dashboard-refinement-evidence/tests.json`, `meralco-reference.json` | Actual parsed test/browser results and external-reference provenance |
| `docs/dashboard-refinement-evidence/admin-overview-1440.png`, `admin-overview-360.png`, `tenant-overview-1440.png`, `tenant-overview-390.png`, `admin-navigation-360.png`, `admin-rates-1440.png`, `admin-rates-390.png` | Seven real final Chromium captures |

## Review, deployment and limits

Review the [dedicated comparison](https://github.com/CZKDYN/THESIS-/compare/feature/website-experience...feature/dashboard-refinement?expand=1) against the website branch. Existing website/stabilization integration review is still required before a main release. The CI trigger includes this integration target; hosted CI execution is not claimed by these local results.

No backend/API/model/migration/gateway/production dependency/Render configuration was modified. No new environment values, migrations or workers are required. The native MQTT network stress test and SQLite portable suite were not repeated for this presentation-only change; current PostgreSQL billing/auth/ingestion regressions and gateway unit tests passed. Physical meter/Pi behavior, real-data forecast accuracy, provider inbox delivery, hosted capacity and full assistive-technology behavior remain outside this local verification.

For an approved release: review the branch and hosted checks, integrate through the reviewed website/stabilization branches, back up the normal deployment, and deploy the approved commit through the existing Render process. Refresh browser assets and verify administrator/tenant sign-in, navigation, logout, rate reference and actual meter states on staging. Preserve current utility confirmation requirements and source records. No automatic merge, Render deployment, production data/secrets change or environment publication was performed.
