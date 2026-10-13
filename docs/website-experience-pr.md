# Improve DormEnergy operational dashboards and accessible reporting

Base: `feature/software-stabilization`  
Head: `feature/website-experience`

The dashboard previously counted only the newest requests, represented credit on a fixed PHP1,000 bar, and offered truncated histories without filters. It now shows complete scoped pending totals and current operational tasks, actual tenant credit/thresholds, observed usage and historical ledger deductions, and filtered paginated requests/transactions with matching CSV export.

Device cards separate measurement time from backend receipts; missing values remain unavailable. Forecast tables expose XGBoost and baseline results, sample-count limitations and recursive metrics, including a caution when persistence performs better. Responsive typography, keyboard navigation/dialogs, chart text/data tables and serialized polling preserve focus/forms and recorded data during network failures.

Read-only API fields and two protected history GET endpoints are additive. Schema stays `d21ae48f397c`; no new migration, production dependency, billing/account/email/forecast algorithm or gateway control is introduced.

Validation: 145 PostgreSQL backend tests; portable 136 passed/9 PostgreSQL-only skips; 68 gateway unit tests plus authenticated MQTT/TLS/HTTPS/PostgreSQL regression with owner/three tenant browser checks; 10 real-mode, 7 memory-email, 8 HTTPS, 4 forecast and 13 new grouped browser checks. Fifteen axe page/dialog scans report zero violations. Local release supervision/schema/disposable restore passed. Representative real browser screenshots, source inventory, exact commands, query-cost evidence and unverified field/hosting limits are in [the implementation report](website-experience.md) and [evidence index](website-evidence/README.md).

Review manually with assistive technology and run hosted CI before release. Do not merge into main or deploy Render as part of this PR preparation. GitHub API access is blocked in the current environment; this body and the [comparison form](https://github.com/CZKDYN/THESIS-/compare/feature/software-stabilization...feature/website-experience?expand=1) are prepared for creating the PR.
