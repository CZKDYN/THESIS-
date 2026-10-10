# DormEnergy API contract

Browser APIs use the authenticated Flask session, server-side role checks, and `X-CSRFToken` for POSTs. Obtain the token from the dashboard's `csrf-token` meta element. Do not use browser session authentication for device traffic.

| Method | Path | Access / behavior |
| --- | --- | --- |
| GET | `/health` | Database connectivity and active environment mode. Not a functional readiness check. |
| GET | `/api/snapshot` | Admin: all units. Tenant: assigned unit only. Optional `unit_id` is authorized server-side. |
| POST | `/api/load-requests` | Tenant submits `amount`, optional `note`, and unique client `reference`. Unit comes from the session, not the payload. |
| POST | `/api/load-requests/<id>/review` | Admin; `decision` is `approved` or `rejected`. Second review returns 409. |
| GET | `/api/transactions/export` | Scoped CSV, optional authorized `unit_id`, at most 10,000 records. |
| GET / POST | `/api/rates` | Admin; history or `{ "rate": "12.85", "effective_at": "future timezone-aware ISO timestamp" }`. Omit effective date for now. |
| POST | `/api/units` | Admin; `name`, `room`, `device_id`. Creates a hardware device and zero-credit account. |
| POST | `/api/units/<id>/tenant` | Admin; `name`, `email`, `password` (12–128 chars). Creates an account in an unassigned unit. |
| POST | `/api/iot/readings` | Device bearer authentication, scoped to the registered device and unit. |
| GET | `/api/iot/instructions` | Device bearer authentication; returns an empty command list and `control_enabled=false`. |

## Hardware transmission

Register and provision the device first. Send the following via HTTPS to your deployment. Decimal fields can be strings to preserve precision. Each reading needs a stable event ID retained across retries.

```http
POST /api/iot/readings
Content-Type: application/json
X-Device-ID: ESP32-004
Authorization: Bearer <securely-provisioned-device-token>
```

```json
{
  "device_id": "ESP32-004",
  "unit_id": 4,
  "event_id": "esp32-004-boot-a-seq-000001",
  "meter_epoch": "meter-installation-a",
  "timestamp": "2026-10-10T07:00:00+00:00",
  "voltage": "230.400",
  "current": "0.9250",
  "power_w": "212.000",
  "energy_kwh": "12.350000",
  "connection_status": "connected"
}
```

Replace the example timestamp with the actual measurement time. Valid supply states: `connected`, `disconnected`, `protective_trip`, `unknown`. Maximum input ranges: 300 V, 100 A, 25,000 W, 1,000,000,000 kWh. Measurements must be finite and nonnegative. Timestamps must include a timezone and cannot be more than two minutes in the future; keep device clocks synchronized.

Success is **201**, with `accepted`, reading `id`, and `delta_kwh`. The first reading is a zero-cost baseline. **401** means invalid authentication; **403** means wrong device/unit/source; **409** means duplicate or out-of-order reading; **422** means validation failure. A rejected reading changes neither balance nor transactions.

For offline synchronization, retain unsent measurements on the Raspberry Pi with their original timestamps and event IDs. Replay oldest first. A retry of an accepted event returns 409 without charging again. The gateway must distinguish the duplicate message from an ordering conflict; reconcile its queue with acknowledged event IDs. Offline samples remain historical; recent receipt does not make an old measurement appear live. The prototype does not implement the physical gateway's queue or firmware.

For a confirmed physical meter reset, use a **new** stable `meter_epoch` and `reset_confirmed=true`. The backend logs a meter-reset event and establishes a zero-cost baseline. It does not guess consumption during the reset gap. Do not change the epoch on an ESP32 restart if the meter's cumulative counter has not actually reset. A counter decrease without confirmation is rejected.

## Billing behavior

- Numeric columns store fixed-point Decimal amounts. Energy deltas and monetary ledger entries retain six decimal places; UI balances usually display two. Sub-cent ledger deductions display six places and CSV preserves exact values.
- PostgreSQL row locks serialize account and device updates. Balance, reading, and corresponding transaction commit together. Reviewed requests and linked reading/credit transactions have database-level unique constraints.
- Electricity rates have effective dates. Intervals spanning a rate change distribute interval energy proportionally across elapsed time at each applicable rate. This assumes uniform consumption **within that interval**; send frequent readings to reduce uncertainty. A stored historical transaction is never recomputed with the latest rate.
- Exhausted credit records a review-required condition. Hardware status remains the observed device status; the server does not invent a physical trip or issue an actuator command. Negative balances retain any measured overrun until the adviser-approved policy is defined.
- Simulation uses this same billing service. Only its clearly synthetic supply state may disconnect on exhausted credit or reconnect after a top-up. A simulated protective trip is never automatically reclosed.

Use the scoped CSV export and structured `meter_rejected` / `simulation_transmission` logs for software evaluation. Device tokens, production passwords, and `.env` contents must never enter test records.
