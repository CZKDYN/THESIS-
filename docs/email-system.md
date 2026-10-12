# Email onboarding and notification implementation

This update implements Resend delivery behind an encrypted PostgreSQL outbox. Development/test delivery is separate and sends no real email. Existing passwords, balances, device credentials, forecasting artifacts and user access flags are preserved. Provider acceptance is tracked; inbox delivery is not inferred.

## Exact deployment steps for the existing Render website

1. Back up the managed PostgreSQL database through the hosting/provider process before a release. Keep the existing DATABASE_URL, SECRET_KEY and ADMIN_SETUP_TOKEN values. This update does not replace them or seed data.
2. Create/sign in to a Resend account. Add a sender domain you own and apply the exact DNS records shown by Resend. Wait for domain verification. Configure SPF/DKIM as instructed; choose your DMARC policy. Use a dedicated sending address, such as `DormEnergy <no-reply@yourdomain.com>`. Disable click tracking for account-link emails so their URL fragments are preserved.
3. Create a Resend key with sending access to the chosen domain. Enter it directly in Render's secure Environment settings as RESEND_API_KEY. Never send it through chat or commit it.
4. Add EMAIL_BACKEND=resend, EMAIL_FROM with the verified address, and PUBLIC_BASE_URL=https://dormenergy.onrender.com. Generate a stable encryption key using the command below and add it securely as EMAIL_ENCRYPTION_KEY. Do not change the signing key to enable this feature.
5. Choose Manual Deploy → Deploy latest commit and wait for Live. The startup applies the migration and runs separate web, forecasting and email workers. Existing administrators keep their sign-in credentials. Newly configured email does not send welcome messages to historical accounts.
6. Sign in, update the administrator's contact address if it is still a placeholder, and inspect Notifications → Email delivery. Assign a test tenant using your own test inbox, verify the invitation arrives, open the complete link, choose a password and confirm tenant isolation. Then test Forgot Password? and the confirmation email. This real provider/inbox check remains an operator step; automated tests do not send actual emails.
7. Enable invitations for actual tenants. Monitor failed/pending records and provider logs. A queued record is not evidence of sending, and Provider accepted is not proof of inbox delivery.

Generate the dedicated key locally with the installed dependency and place its output only in secure hosting settings:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Do not rotate that key or the provider account while messages are pending. Drain or cancel outstanding messages first. Database backups containing email ciphertext need the correct encryption key for restoration.

## Environment variables

| Variable | Default / purpose |
| --- | --- |
| EMAIL_BACKEND | `disabled`; choose `resend` for real mail or `memory` only for explicit local tests. |
| RESEND_API_KEY | Required for Resend, entered securely. Never exposed through APIs/logs. |
| EMAIL_FROM | Verified sender address, optionally `DormEnergy <address>`. |
| PUBLIC_BASE_URL | Required explicit canonical HTTPS origin; no request-host-derived links. HTTP is accepted only for loopback memory/testing. |
| EMAIL_ENCRYPTION_KEY | Recommended stable Fernet key. If absent, a separate-purpose key derives from SECRET_KEY; changing that signing key can make queued ciphertext unreadable. |
| EMAIL_ACTIVATION_HOURS | 24; accepted range 1–168. |
| EMAIL_RESET_MINUTES | 30; accepted range 1–120. |
| EMAIL_RESEND_COOLDOWN | 60 seconds; invitations/recovery also have persistent per-account quotas. |
| EMAIL_MAX_ATTEMPTS | 6; bounded to 1–10 worker attempts. |
| EMAIL_CREDIT_EXHAUSTION | `false`; optional real-reading alerts to the tenant and opted-in active administrators. No relay action. |
| ALLOW_TEST_EMAIL | `false`; explicit opt-in required for a non-testing memory backend. |

The existing Render Blueprint is left unchanged to avoid replacing production settings automatically. Add the email values manually. Resend is the implemented provider; another provider would need a tested adapter.

## Account workflows

Default tenant assignment needs a name/email/unit and creates an inactive pending account with an unguessable placeholder password. The worker sends a branded HTML/plain-text invitation with room, website and expiration. The tenant creates their own password. No administrator-chosen password is sent by email.

Pending accounts reserve their assignment and show Pending or Expired separately from delivery state. Resend Invitation invalidates the old token and queued message before issuing a fresh link. Requests have a cooldown and five-per-hour account quota. A disabled/misconfigured provider leaves the account pending and shows Not sent / failed explicitly. The administrator may configure mail and resend, or deliberately select the compatible temporary-password fallback; that fallback activates access without claiming email verification and still requires a first-login change.

Existing accounts stay active/inactive as before. Existing administrator setup remains protected by the hosting secret and closes after the first active administrator exists. Configured delivery sends a welcome only for new setup, password-change notices, and email-change notices to both old/new addresses. These contain neither passwords nor setup tokens. Changing an email invalidates old links and clears its verified timestamp.

Forgot Password? uses the same generic response for unknown, inactive, pending, former or eligible addresses. Eligible active accounts get a 30-minute single-use reset link. Password reset revokes sessions and other links and sends a confirmation when configured. It cannot create an administrator or choose a unit. Former tenants and archived/demo units cannot activate/recover access. Ending tenancy or archiving cancels outstanding links/messages; replacement tenants get their own records/recipients.

Account links carry 256-bit random secrets in URL fragments. The browser strips the fragment from the address bar and posts it in the CSRF-protected body. GET requests do not consume links, so an ordinary email scanner cannot activate an account. Pages use no-store and a same-origin referrer policy: HTTPS form protection receives the same-site referrer, external sites receive none, and fragments never enter the referrer. JavaScript is required; copy the complete link including the fragment if the email button fails.

## Billing and preferences

The existing row-locked threshold-crossing logic queues one low-balance alert when credit moves from above to at/below the threshold. Readings while already below do not repeat it. Replenishing above the threshold naturally rearms the next crossing. Reading/event uniqueness and outbox event keys prevent duplicate notifications.

Approved requests snapshot the approved amount, updated balance, reference, room and Manila review time; rejected requests direct the tenant to the dashboard without unnecessary detail. Outbox insertion and the financial update commit or roll back together. No provider call happens in the billing transaction. Historical packets older than five minutes and readings before the current tenancy do not create new balance emails; explicit import jobs can set `db.session.info['suppress_email']=True`.

Users control low-balance/exhaustion and load-update messages in Your account. Security notices have no opt-out. A worker rechecks preferences, active tenancy, recipient address, token expiry/version and assignment before sending. Recipients/event data are captured when queued, never resolved to a replacement tenant.

## Delivery and failure handling

Account token records store SHA-256 hashes, not raw secrets. To allow reliable delayed sending, the outbox stores authenticated encrypted message bodies containing the private link. It exposes neither that ciphertext nor plaintext/token/recipient through the status API. Keep the encryption key separate from database backups where possible.

A separate email worker claims pending rows using PostgreSQL SKIP LOCKED and a two-minute lease. It commits the claim and releases database transactions before provider I/O. A crashed lease is recoverable. Temporary network/408/429/5xx failures retry after 60, 300, 900 and then 3600 seconds; permanent failures remain failed. Attempts use one global message key derived from immutable ciphertext, avoiding collisions between deployments. Sender/body snapshots stay stable across retries. Retry age is capped at 22 hours to remain inside the provider's documented idempotency window; requests beyond it fail visibly rather than risking duplicates.

The fixed Resend endpoint uses TLS verification, a ten-second timeout and no redirects, so authentication cannot be forwarded to another host. Logs contain only numeric job ID, attempt, status and safe error code. No provider response bodies, API keys, tokens or email text are logged.

States: Pending, Sending, Provider accepted (`sent`), Not sent / failed, Cancelled, and Test only (`test`). Provider accepted means the API returned a message identifier. No delivery/bounce webhook is implemented, so actual inbox delivery must be verified separately. An email already accepted or in flight can still arrive after an account change; cancellation cannot recall it. Its captured content never contains a replacement tenant's data, and its old token is still invalidated.

The memory backend records test-only messages in process memory and marks them test, never sent. Automated test apps default to this backend regardless of production environment settings. Tests use explicit mocked HTTP transport for provider adapter/acceptance/failure cases; no real messages are sent.

## Database migration

Revision `c9046fd275ad`, following `b782410cdf65`, adds four user fields: activation status, email verification time, and two nonessential preference flags. Existing status defaults to active without changing the existing active flag, password hash or session version.

New tables are account_tokens (hash, scope/version/address snapshots, expiry/use/revocation), email_outbox (encrypted payload, unique event key, delivery state/attempt/lease), and email_rate_limits (hashed quota keys and unique time windows). No migration invokes sending or backfills notifications. Downgrade drops new email/token records; back them up and do not use downgrade as routine recovery.

The retained database upgrade was checked against fourteen existing tables. Record counts, password hashes, identities/access/session flags, balances/transactions, device token hashes and model JSON/version data were unchanged. The environment file was also unchanged, no messages were created, and Alembic schema comparison detected no extra operations.

## Validation and limitations

The email/HTTPS release passed 68 PostgreSQL tests: the prior 38 regressions, 21 email cases and nine sign-in/HTTPS security cases. Its portable run passed 62 with six expected PostgreSQL-only skips. Seven email browser checks and ten existing real-mode browser workflows passed; subsequent real local HTTPS checks cover secure-cookie activation and recovery. Later utility-rate checks extend that coverage; see [the current full test record](testing.md). Evidence remains in ignored artifacts/https-full-tests.xml, https-portable-tests.xml, browser-https-smoke.json, browser-email-smoke.json, browser-real-smoke.json and email-migration-preservation.json.

No actual Resend key/verified sender was available during implementation; real provider acceptance/inbox delivery and the latest Render deployment/hosted CI are not verified. Hosted throughput/memory, penetration testing, inbox/bounce processing and provider policy/limits need operational validation. Free hosting sleep pauses both background workers. Recovery limits use the server-observed source address; a shared reverse-proxy address can share its quota. Configure a trusted proxy strategy before widening limits or trusting forwarded headers.

## Troubleshoot

- Not configured / configuration failure: check the five delivery values, HTTPS root origin, sender domain, API-key permission and Fernet key. No token body is shown in the dashboard.
- Pending: confirm `email-worker` is running and the host is awake. Local log: .dormenergy-cloud/email.log. Render logs show safe email_attempt records.
- Provider rejected: verify sender domain/API permission and inspect provider-side diagnostics securely. Do not paste full responses or secrets into chat.
- Temporary failure: allow scheduled retries; pending retry is not sent. Repeated outages end as failed after the limit.
- Encryption failure: restore the original key. Do not replace secrets blindly. Regenerate an invitation/reset link after correcting configuration.
- Expired/replaced/used link: request a fresh invitation or recovery link. Complete fragments must be preserved by the email client; disable click tracking on account links.
- Former/archived tenancy: links and queued mail are cancelled; restore the actual unit deliberately if appropriate, then resend a pending invitation. A former tenancy is not reactivated.

## Changed file inventory

New: dormenergy/services/emailing.py; dormenergy/email_links.py; dormenergy/templates/email_account.html; dormenergy/templates/emails/message.html and message.txt; dormenergy/static/js/account-links.js; migrations/versions/c9046fd275ad_email_onboarding_and_outbox.py; tests/test_email_onboarding.py and test_login_recovery.py; scripts/browser_email_smoke.py and browser_https_smoke.py; docs/email-system.md.

Modified: dormenergy/__init__.py, models.py, api.py, web.py, commands.py; services/accounts.py and billing.py; templates/account.html, dashboard.html and login.html; static/js/dashboard.js and static/css/app.css; requirements.txt and requirements.lock.txt; scripts/start-render.sh, start-cloud.sh, stop-cloud.sh and browser_real_smoke.py; tests/test_workflows.py; .env.example; README.md; docs/api.md, deployment.md, real-mode.md and testing.md. Hardware.py and the XGBoost algorithm are unchanged.
