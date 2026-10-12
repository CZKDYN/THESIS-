# Operate DormEnergy with actual tenants and meters

## Move the existing website out of demonstration mode

Deploy the latest GitHub commit in Render and wait for Live. The new entrypoint applies migrations, forces real mode, and starts no simulator. Existing demo units are archived, their pending requests rejected, and demo tenant access disabled. Readings, money, transactions, accounts, and XGBoost artifacts remain stored. Use Previous demonstration history to export them. Archived sample data are never relabeled as real readings.

Continue signing in with your existing administrator email/password. Open Your account to set your own contact email and change your password. A password change signs out other sessions. On a completely fresh installation instead, use the hosting-secret ADMIN_SETUP_TOKEN at /setup to create the first administrator. No publicly known owner password is supplied.

## Register occupied units

Open Dormitory units, choose Register unit, and enter its name, room, and unique physical-meter identifier. New accounts start with zero credit and unknown supply state. Edit unit changes its name/location and low-credit warning threshold without changing the meter identity or ledger.

Electricity rates now shows a locked Meralco PHP 14.74/kWh reference for East Rembo, Taguig, supplied by the owner and awaiting bill confirmation. It is not automatically a latest or regulated tariff, and it creates no charges. Confirm the actual applicable utility source/period and activate its rate through [hosting configuration](utility-rates.md) before meter ingestion. Browser/API price changes are blocked in locked mode. Historical deductions are not recomputed. The system rejects intervals with missing, expired or gapped utility coverage; a seeded or legacy manually entered tariff cannot substitute for a confirmed source.

## Create tenant accounts

Choose Assign tenant, enter the tenant's name/email, and use Send email invitation. The tenant receives a single-use activation link and creates their own password. Until activation, the account stays pending. Inspect the separate email delivery status; queued is not sent. Resend Invitation safely replaces the old link. Configure Resend first using the [email guide](email-system.md).

For the compatible manual fallback, deliberately select Use a temporary password instead. Choose 12–128 characters and share securely. The tenant must replace it at first sign-in. This fallback does not verify email ownership. There is no public registration that lets someone choose another tenant's unit.

Manage tenant lets the administrator set a replacement temporary password. Existing tenant sessions are revoked immediately; the tenant must again choose their own password. Forgot Password? supports eligible administrator and tenant accounts with expiring single-use links when delivery is configured. Otherwise use the trusted hosting/database recovery process.

When the tenant moves out, choose End tenancy and confirm. The old login loses access, its pending credit requests are rejected, and its identity/records remain stored. Assign the next tenant afterward. New tenants do not receive the previous tenant's credit-request list or pre-tenancy transaction export. Meter consumption remains unit history.

Credit belongs to the unit's prepaid account. Ending a tenancy does not erase, transfer, or refund its balance. Settle any carryover/refund according to the dormitory's policy before assigning the next tenant. The website does not implement external refunds or payment processing. Users can manage nonessential email preferences in Your account; security notices remain enabled.

## Collect credit requests

Tenants submit a load request after using the dormitory's accepted payment method. The administrator verifies the payment separately, then approves or rejects the request. Approval adds credit once and records a ledger entry; repeated approval is rejected. This is a working manual approval workflow, not automatic payment verification.

## Connect a physical meter

In Device monitoring, choose Generate device token for the unit. Confirm your administrator password. Copy the one-time token onto your ESP32/Raspberry Pi gateway and keep it secret. Closing the dialog clears it; the server stores only a hash. Replacing/revoking the token invalidates the old credential.

Send POST requests over HTTPS to `/api/iot/readings`, using `X-Device-ID` and `Authorization: Bearer <token>`. Include the registered unit/device IDs, stable event ID, meter epoch, original timezone-aware timestamp, measured voltage/current/power, and cumulative kWh. See the complete [API payload](api.md).

The first accepted reading establishes a zero-cost baseline. Subsequent ordered increases deduct measured energy at the applicable rate. Duplicate/out-of-order events cannot bill twice. Confirmed counter resets need a new meter epoch. Offline gateway replay must retain original timestamps and event IDs.

Until a meter sends data, the dashboard shows awaiting readings and unavailable measurements. It does not create sample consumption. Live status needs both recent receipt and a fresh measurement. The server monitors reported supply state; it does not issue physical breaker commands.

## Use the AI forecast

After at least 720 covered hourly observations from the unit's hardware meter are available, select that unit, open Forecasting, and choose Train XGBoost. Training runs in the worker. Inspect measured held-out metrics, baselines, and forecast status. Missing data or insufficient recent coverage can leave predictions unavailable. Credit duration is bounded by the stored forecast horizon and is not a guaranteed expiration time. Read [the evaluation method](forecasting.md).

## Remove or restore a unit

Remove unit archives an actual unit and blocks its tenant and meter ingestion while retaining records and balances. Archived units lets you export its ledger or restore it. Restore reactivates only its current tenant; a former tenant is not restored. Old rejected requests stay rejected. Retired demonstration units cannot be restored into the real workspace.
