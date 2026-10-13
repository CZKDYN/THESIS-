# Website review evidence

14 October 2026, Asia/Manila. Actual local software tests on the dedicated website branch. See [the full report](../website-experience.md) for commands, exact scope, limitations and the release checklist. These records do not establish a Render deployment, physical meter operation, real payment, actual email delivery or real-world forecast accuracy.

Before screenshots were captured with the unchanged stabilization interface using the existing isolated real-mode browser fixture. After screenshots use a separate temporary three-unit synthetic fixture and are explicitly labeled Simulation environment. Different values/datasets are intentional and are not a quantitative improvement claim. Fake fixture usernames/emails and masked inputs are test-only. Test credentials, provider keys, link secrets and production records are not included.

| Screen | Before | After |
| --- | --- | --- |
| Administrator desktop | [Capture](before-admin-desktop.png) | [Operations center, 1440px](admin-overview-1440.png) |
| Tenant phone | [Capture](before-tenant-mobile.png) | [Tenant overview, 390px](tenant-overview-390.png) |
| Administrator phone | [Units](before-units-mobile.png) | [Operations, 360px](admin-overview-360.png) |
| Empty workspace | [Capture](before-empty-workspace.png) | Empty data tested by existing real-mode workflow and API regressions |
| Financial history | Earlier histories lacked paging/filters | [Transaction history, 360px](admin-history-360.png) |
| Forecasting | Earlier comparisons were dense prose | [Metrics/baselines/recursive tables, 1440px](forecast-1440.png) |
| Device evidence | Earlier receipt wording implied Pi health | [Separate measurement/receipt evidence, 1440px](devices-1440.png) |

All five widths (360/390/768/1024/1440) have actual captures in ignored `artifacts/website/`; these six representative after images are committed. No screenshot was edited into a mockup.

| Record | Meaning |
| --- | --- |
| [tests.json](tests.json) | Parsed final test counts, actual browser reports, runtime and execution boundaries |
| [accessibility.json](accessibility.json) | Final 15 rendered page/dialog scans; no reported violations |
| [dependency-final.json](dependency-final.json) | Verified-TLS PyPI release-advisory results for exact active pins, including browser tooling |
| [performance-before.json](performance-before.json) | Prior stabilization benchmark; different execution time |
| [performance-after.json](performance-after.json) | Current 9,999-reading/three-unit benchmark, including extra read-only summary cost |
| [network.json](network.json) | Actual authenticated local MQTT/HTTPS/PostgreSQL outage/recovery/duplicate-billing results |
| [release-check.json](release-check.json) | Local Render supervisor/restart, schema and disposable PostgreSQL restore results |

Raw logs/JUnit and early failing contrast evidence remain in ignored local artifacts. No hosted CI pass or automatic PR/merge/deployment is claimed. Automated accessibility checks are partial evidence and do not certify all WCAG requirements or assistive technology behavior.
