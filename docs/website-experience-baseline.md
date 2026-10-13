# Website experience baseline

Review date: 14 October 2026 (Asia/Manila). Source: stabilization commit `d7527b48080cadbff36db7b221bb72eb6ed40202`; dedicated branch `feature/website-experience`, targeting `feature/software-stabilization`. The initial worktree was clean and the remote stabilization SHA matched. Main, Render, production data/secrets and the gateway package are preserved.

Inspected dashboard templates/styles/scripts, authentication/account/link pages, Flask API/model fields, existing regression/browser helpers and audit/forecast/deployment guides before implementation. Existing secure setup/login, account lifecycle, rate protection, billing, unit management, device tokens, forecasts and encrypted email workflows are reused.

Observed opportunities:

- Balance bar uses a fixed PHP1,000 scale that has no account-policy meaning.
- Overview mixes historical notifications with current low-credit/offline warnings.
- Pending count is derived from the latest 100 requests instead of all scoped pending records.
- Histories show only the latest 40 transactions/100 requests, without filters or pagination.
- Device wording calls recent API receipts a Raspberry Pi gateway state/synchronization assessment, although no authenticated gateway-health endpoint exists.
- Missing numeric values may be formatted as zero; chart gaps lack accessible textual summaries.
- Forecast comparisons are dense prose, without a clear warning when a baseline outperforms XGBoost.
- Repeated innerHTML replacement can destroy keyboard focus; request filtering can race with an in-flight snapshot.
- Network errors retain some data but lack explicit recovery controls and status context.
- Several desktop/mobile labels are 7–11px with weak contrast; mobile navigation needs keyboard focus management.

The isolated legacy real-mode Chromium workflow provides the before screenshots and confirms existing operations. New screenshots will use clearly labeled disposable fixtures. No live site is exercised destructively, and no mock reading or real email goes to production.
