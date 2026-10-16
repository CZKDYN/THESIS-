# Refined dashboard screenshots and evidence

Actual Chromium screenshots on 16 October 2026, Asia/Manila, using disposable three-unit software fixtures. The visible Simulation environment badge identifies synthetic measurements; these captures do not establish real-meter operation, payment or field forecast accuracy. No image was edited into a mockup. Full commands, limits and the complete file inventory are in [the implementation report](../dashboard-refinement.md).

| Interface | Screenshot |
| --- | --- |
| Administrator desktop | [1440px overview](admin-overview-1440.png) |
| Administrator phone | [360px overview](admin-overview-360.png) |
| Tenant desktop | [1440px overview](tenant-overview-1440.png) |
| Tenant phone | [390px overview](tenant-overview-390.png) |
| Mobile navigation/profile | [360px drawer](admin-navigation-360.png) |
| Rate reference on desktop | [1440px electricity rates](admin-rates-1440.png) |
| Rate reference on phone | [390px electricity rates](admin-rates-390.png) |

For the previous sidebar/footer, see the earlier [administrator](../website-evidence/admin-overview-1440.png) and [tenant](../website-evidence/tenant-overview-390.png) captures. These use separate disposable fixtures/dates, so numerical differences are not evidence of changed billing or consumption. All five tested widths remain available in ignored `artifacts/dashboard-refinement/`.

- [tests.json](tests.json): 145 backend and 68 gateway unit tests, 46 grouped browser checks, 18 accessibility states with zero reported violations, and actual runtime/fixture boundaries.
- [meralco-reference.json](meralco-reference.json): exact selected official monthly advisory, owner-confirmed access, rejected schedule candidate and the automated proxy block. Opening a resource does not establish tariff applicability.

Neither hosted CI nor Render deployment is claimed. Automated accessibility scans do not replace a full manual screen-reader/WCAG evaluation. The Meralco link is month-specific and may need a future reviewed update.
