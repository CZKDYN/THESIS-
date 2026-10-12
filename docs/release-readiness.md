# Controlled release readiness and rollback

Candidate: `feature/software-stabilization`, gateway package **0.1.1**. Status: ready for engineering review and an isolated/staging software-to-hardware integration trial after the gates below. **No merge or Render deployment has been performed.** [Audit](software-audit.md), [validation](software-validation.md) and [small committed evidence](audit-evidence/) contain the actual tests and limits.

## Review and deploy gates

1. Review the whole branch against main, including the previously unmerged gateway receipt endpoint. Review stabilization separately against gateway commit `4c7dc68a6fbd1c4601f143e2f6682f7569b89502`. Record the approved commit SHA rather than deploying an unspecified latest HEAD.
2. Run the configured GitHub Actions checks on that commit and retain results. Local Linux evidence exists; external Actions, Windows/Python3.11, Pi ARM and Docker runs remain unverified in this environment. A source push or PR preparation does not satisfy hosted checks.
3. Rehearse in a **separate staging service/database**, with new test-only credentials, the approved backend and gateway versions, verified TLS and actual migrated schema. Never send mock ESP32 samples or `gateway_fixture` bodies to production. Validate three rooms, units/roles, original timestamps, application ACKs, receipts, queues and every relevant repair procedure.
4. Inventory/preserve existing accounts, balances, transaction/reading counts, rate/model records and migration head before any approved live release. This audit has no new PostgreSQL migration; schema remains `d21ae48f397c`. Migration startup must run against the intended database with a least-privilege application role. Never run any `_test` clearing script against retained/live data.
5. Make an encrypted, access-controlled PostgreSQL backup and a consistent gateway SQLite backup before release. Rehearse restoration into a **different disposable database**, check ledger balances/counts/schema/application reads, and document storage/retention. The audit's local test backup rehearsal is not evidence that the managed production backup can be restored.
6. Preserve stable `SECRET_KEY`, `EMAIL_ENCRYPTION_KEY` and account/device credentials across deployments. Store them only in hosting/protected Pi settings. Keep `SIMULATION_MODE=false`, debug off and `SESSION_COOKIE_SECURE=true` on HTTPS. No automatic demo seeding, synthetic training or simulator is allowed. Review environment parsing before startup; new forecast age settings default to due 7/max 30 days and must obey 1 <= due <= max <= 365.
7. Keep utility confirmation false until the owner verifies the applicable main-meter provider/class, billed PHP/kWh, source/month and valid effective/exclusive-end dates. PHP14.74 is an unverified owner reference, not regulatory approval. Use the documented [utility workflow](utility-rates.md); missing/expired coverage intentionally holds meter uploads atomically. No retrospective tariff rewrite is permitted to repair a queue.
8. Keep email disabled until the owned sender domain and Resend credentials/configuration are ready. Follow [the email guide](email-system.md) for an operator-authorized test inbox invitation/reset, and separately inspect actual inbox receipt/expiry/single-use/session revocation. Do not send automated real messages to tenants during checks. Stable queued-message encryption and provider idempotency windows must survive restarts/backups.
9. Confirm the hosting plan supports the web and two required background workers, database lifetime/backups, connection limits, storage and memory. Free web service sleep pauses workers and may delay uploads/emails/jobs; free database retention/expiry may change. Verify current provider limits directly. There is **no 24/7 reliability claim**. An always-on plan and measured hosted recovery/capacity are required before promising continuous operation.
10. Rehearse `scripts/start-render.sh`: migrations/apply-rate, one Gunicorn worker initially, separate forecast/email workers, `/health` DB check, child-failure service restart, no simulator. `/health` verifies web/database, not successful email delivery, model quality or an external Pi. Observe provider logs and do not mistake a healthy endpoint for completed background work.
11. Install reviewed gateway software/credentials/CA/ACL on the Pi using [installation](../gateway/docs/installation.md). Confirm room topics, unit IDs, stable meter epochs, UTC clock/NTP, protected token files, dedicated service user/single-instance lock, monitoring/storage capacity and consistent backup. Device tokens rotate/revoke; they do not expire by time. Test old tokens rejected and new tokens provisioned before controlled resume. Never run the mock publisher in real mode.
12. Validate the future firmware's durable journal/application ACK, counter/time preservation, reset approval and behavior during MQTT/storage outages. The gateway does not fabricate samples, actuate breakers or supply physical measurement calibration. Actual Pi ARM, SD durability/power loss, Wi-Fi/NTP drift, PZEM calibration and occupied-building trials remain hardware work.

Production promotion and merging require user/reviewer authorization after these concrete checks. These instructions are a checklist for the authorized operator; they do not trigger deployment automatically.

## Backup and restore procedure

PostgreSQL (trusted terminal, credentials supplied securely by the operator, never pasted into source/logs):

```bash
# PGHOST/PGUSER/PGDATABASE and managed TLS settings must already identify the reviewed source.
pg_dump --format=custom --file=/secure-backups/dormenergy-release.dump
# Create a NEW empty disposable restore database using the operator's managed tooling.
pg_restore --no-owner --exit-on-error --dbname=dormenergy_restore_test /secure-backups/dormenergy-release.dump
```

Inspect every restored table count, account balance/ledger sum, identities/source labels, migration head and sample scoped reads before accepting the backup. Backups contain private account hashes, tokens/ciphertext and consumption history; encrypt them, restrict access and retain the encryption keys separately. Never overwrite the active managed database as a restore test. `scripts/audit_release_check.py` demonstrates this process with disposable fixtures only.

Gateway:

```bash
dormenergy-gateway backup --config /etc/dormenergy-gateway/config.json --destination /secure-backups/gateway-release.sqlite3
dormenergy-gateway restore-check --backup /secure-backups/gateway-release.sqlite3
```

Use the SQLite backup API rather than copying an active database without its WAL. Keep profile binding, original event/canonical content, receipt proofs and separately secured credential/config files. In a staging restore check, disable cloud transmission to avoid replaying to the wrong destination. Receipt proofs are intentionally retained after old sent payloads are purged; monitor disk size/free space and plan storage growth. Reaching configured limits refuses new acceptance ACKs and relies on the upstream firmware journal; it never purges unsent events to keep running.

## Controlled rollback

1. Pause physical publishers only through an approved firmware procedure that retains unacknowledged events; stop the gateway upload service safely and preserve/backup its journal. Never delete queues or change event/timestamp/unit/epoch values to bypass a conflict.
2. Record the failed/new and previous approved commit SHAs, current schema/head, queue states and non-sensitive logs. Stop/drain web and workers through hosting controls before rollback to avoid mixed versions.
3. Deploy the previously approved compatible source/configuration using the existing production database/keys. This stabilization release has no PostgreSQL schema downgrade requirement. Old forecasts remain compatible, and the added SQLite index is backward compatible. Rollback to pre-gateway `main` lacks the receipt endpoint: ambiguous 409 uploads will remain held until the reviewed receipt-capable backend is restored. Do not interpret such conflicts as delivery success.
4. Start the approved services, confirm hardware mode, `/health`, database access, required workers, stable login/CSRF and zero demo generation. Verify a controlled authentic reading/receipt and unchanged ledger history under operator authorization. Confirm tariff/sender settings remain those approved before the release.
5. Repair the underlying error (certificate, credential, rate, transport, validation, capacity) before `dormenergy-gateway resume --config ... --device <alias>`. Resume preserves original data and rechecks the backend; it does not force acceptance. Unknown/out-of-order conflicts require reconciliation against authoritative receipts/firmware records. Restore backups only for an actual data recovery need, into a separate database first, under a documented cutover decision.
6. For mail worker rollback, preserve outbox encryption/recipient snapshots and stable provider keys. Do not blindly replay already accepted mail or restore stale queues beyond the safe provider idempotency window. Single-use/revoked links must stay invalid; provider/in-flight emails cannot be recalled.

## Remaining release dependencies

Hosted resource/connection limits and long endurance, managed restore, real sender/inbox delivery, legally applicable confirmed utility coverage, Windows/CI/Pi ARM checks, firmware/calibration and field safety remain unverified. The software implements manual payment approval, not a payment processor; unit credit settlement on turnover is a dormitory policy. XGBoost's 720-hour threshold and synthetic test metrics do not establish field accuracy; collect representative real histories and retain unfavorable baseline comparisons. No hardware switching or automatic protective-trip reclosing is implemented.
