# Locked utility rates for East Rembo, Taguig

The owner supplied **PHP 14.74/kWh** as a Meralco reference on 12 October 2026. No processed bill, applicable customer class, official tariff period or source document was supplied. Official EPIRA/Meralco pages could not be retrieved from this execution environment. This value is therefore a **reference awaiting confirmation**, not an independently verified latest tariff, an ERC approval, or a national statutory rate.

EPIRA (Republic Act No. 9136) provides the electricity-sector regulatory framework; it does not supply one universal nationwide PHP/kWh figure. Applicable charges depend on the distribution utility, customer class, consumption/billing conditions and period. A news headline quoting a representative Meralco residential rate should not automatically determine a dormitory main-meter tariff. A software lock alone does not establish permission to resell electricity or certify a tenant-billing arrangement.

Public source links for operator review: [EPIRA text](https://lawphil.net/statutes/repacts/ra2001/ra_9136_2001.html), [Meralco rate information](https://www.meralco.com.ph/residential/billing-payment/understanding-your-bill/rates-archives), and the [Energy Regulatory Commission](https://www.erc.gov.ph/). These links were not successfully retrieved or independently verified during this update. Confirm the actual applicable utility bill/tariff and any ERC requirements before charging real tenants.

## What is locked now

- Electricity rates shows Meralco, East Rembo/Taguig and a read-only PHP 14.7400/kWh reference. The free-form rate/effective-date form is removed in locked mode.
- The server rejects POST /api/rates with 403 even if a browser modifies the HTML or calls the endpoint directly.
- The reference creates no rate record or charge. UTILITY_RATE_CONFIRMED defaults to false. The interface states that no confirmed billing rate is active.
- Historical tariffs remain in history with their original amounts and effective times; they are not relabeled as utility-confirmed rates.
- Actual meter billing uses only source-tagged owner-confirmed utility periods while locked. Missing coverage, expiry or a gap rejects the entire reading/charge transaction. Gateways must retain and retry original packets; there is no fabricated reading or alternative fallback price.
- Consumption learning/forecasting algorithms are unchanged. Only their credit-cost projection checks utility coverage and stops when a future price is unknown.

For visual review, deploy the latest source and keep UTILITY_RATE_CONFIRMED=false and EMAIL_BACKEND=disabled. No domain or Resend setup is needed. Other account/unit/credit actions still save records when submitted; this is not a general read-only website preview.

## Hosting settings

Defaults are safe for the requested visual review. Do not replace unrelated secrets or production settings automatically.

| Setting | Default / purpose |
| --- | --- |
| UTILITY_RATE_LOCKED | `true`; ordinary administrators cannot edit the tariff. Keep true for this dormitory. False exists only for deliberate legacy/manual compatibility and regression fixtures. |
| UTILITY_PROVIDER | `Meralco`; confirm against the actual bill. |
| UTILITY_SERVICE_AREA | `East Rembo, Taguig`. |
| UTILITY_RATE_PHP_PER_KWH | `14.74`; owner-supplied reference, four decimal places maximum. |
| UTILITY_RATE_CONFIRMED | `false`; do not enable until the owner has confirmed the actual applicable source. |
| UTILITY_RATE_PERIOD | Blank; confirmation requires `YYYY-MM` matching the bill/tariff period. |
| UTILITY_RATE_SOURCE | Blank; confirmation requires a non-sensitive source description, including applicable class/period or an official source URL. Never include account numbers or credentials. |
| UTILITY_RATE_EFFECTIVE_AT | Blank applies a new confirmed configuration at startup time. A future timezone-aware ISO instant can be supplied; new configurations cannot be backdated. |
| UTILITY_RATE_VALID_UNTIL | Blank; confirmation requires a timezone-aware exclusive validity end after activation. Use the applicable source period and update it when the next period is confirmed. |

## Activate actual billing later

1. Confirm the main meter's provider, tariff/customer class and applicable period. Use the applicable energy rate or an approved bill-based allocation method. Do not divide a total that includes previous balances, deposits, penalties or unrelated fees and call the result a regulated tariff. Never add an arbitrary per-kWh markup through this website.
2. In Render's Environment settings keep UTILITY_RATE_LOCKED=true. Set the actual confirmed UTILITY_RATE_PHP_PER_KWH, provider, period, source description and validity end. Choose a future effective date or leave it blank to apply now. Set UTILITY_RATE_CONFIRMED=true only after checking these inputs. This flag records the owner's confirmation; the app does not validate an official document automatically.
3. Save and deploy the latest source. Startup runs `flask --app wsgi db upgrade` followed by `flask --app wsgi apply-utility-rate`. The latter appends one new immutable rate per configuration; repeated startups reuse it, with PostgreSQL advisory locking and a unique fingerprint preventing duplicates.
4. Inspect Electricity rates: the active billing rate, source, period and validity dates must match the confirmed source. A reference alone or an expired configuration is insufficient. Test authenticated meter ingestion only against the verified coverage period.
5. For the next period, confirm the new applicable rate, source and dates and repeat. No scraping, automatic rate fetching, silent indefinite carryover or backdating is implemented. Earlier charges are never recomputed or refunded automatically.

Keep historical periods and retain a dated source record outside the app. Local command equivalent: `.venv/bin/flask --app wsgi apply-utility-rate`. If an explicit new effective date has become past before deployment, the command refuses activation; choose a future date or leave it blank. Source descriptions, period/expiry and provider remain part of each rate's history.

## Migration and validation

Revision d21ae48f397c follows c9046fd275ad. It adds five nullable rate fields: provider, billing period, source reference, exclusive validity end and unique configuration fingerprint. It adds interval/uniqueness constraints and does not seed a rate, alter balances, change old rows, send emails or reset credentials. Downgrade removes provenance fields, so it is not routine financial recovery.

Tests cover protected UI/API access, pending references without billing, idempotent/concurrent configuration, unchanged legacy tariffs/records, exact Decimal deductions, expired/gapped coverage rollback, invalid source/date/value rejection, backdating refusal and forecast-cost horizon limits. Existing manual-rate regressions explicitly opt out of locking in isolated test factories; normal deployment defaults to locked mode.

`python scripts/browser_https_smoke.py` exercises the locked display, forged API rejection, desktop/phone layout and existing login/account/activation/recovery flows over real local TLS. It uses only test fixtures and a temporary database. No real email or physical meter is used. See [current actual test results](testing.md).

Evidence includes ignored artifacts/utility-rate-tests.xml, utility-full-tests.xml, utility-portable-tests.xml, utility-migration-preservation.json, browser-https-smoke.json, utility-rate-desktop.png and utility-rate-mobile.png. Latest hosted deployment and the actual Meralco source/rate applicability remain unverified.
