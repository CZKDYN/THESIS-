# Software audit starting state — 2026-10-12

Inspected before stabilization edits. Production/main: `24f6ecf4ff565d7f9ea538c163a5ae8a47d62152`. Gateway feature: `4c7dc68a6fbd1c4601f143e2f6682f7569b89502`. The worktree was clean. Stabilization branch `feature/software-stabilization` starts from the gateway commit; neither existing branch is merged or deployed automatically.

The gateway branch adds 43 files / 2869 insertions / 16 deletions relative to main. Most additions are the modular Python gateway, three-room development tools, tests, secure broker/service templates and documentation. Backend differences are a device-scoped receipt endpoint, production fixture rejection, deep-JSON handling and stable ingestion error codes. Database schema/migration head remains `d21ae48f397c`; billing formulas, accounts, email and forecasting implementation are unchanged at this baseline.

Main lacks the receipt endpoint. First valid uploads work against that backend, but ambiguous 409 responses cannot be resolved there; the gateway holds the affected stream instead of assuming success. Gateway integration must be reviewed and deployed with the backend addition before a real trial. Hosted Render contents, external CI results, live sender deliverability and physical hardware are not assumed verified.

Prior locally recorded evidence is 87 PostgreSQL website tests, 59 gateway unit tests plus one authenticated MQTT/TLS/HTTPS/PostgreSQL integration test, and owner/three-tenant Chromium rendering. These prior runs are distinct from this audit's fresh final validation. No production data/secrets/provider calls are used in this audit.

Review scope: every application/service module, model and migration; dashboard/template scripts; gateway queue, validation, cloud, clock and broker code; CI, Render/cloud startup and dependency pins. Source redesign and new hardware control are out of scope. Pending Meralco reference PHP14.74 remains unverified and cannot authorize charges.
