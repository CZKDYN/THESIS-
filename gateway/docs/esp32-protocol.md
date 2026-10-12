# ESP32 measurement protocol

Each room independently samples its own PZEM-004T. ESP32 firmware is a required later deliverable: the Python gateway/mock does not read UART, provision boards, calibrate meters or safely wire electrical circuits. Do not connect mains based on this software document.

## Topics and identity

| Room | Authenticated broker username | Publish QoS 1 | Read QoS 1 |
| --- | --- | --- | --- |
| Floor 1 room 1 | `room1` | `dormenergy/floor1/room1/telemetry`, `.../status` | `dormenergy/floor1/room1/ack` |
| Floor 2 room 2 | `room2` | `dormenergy/floor2/room2/telemetry`, `.../status` | `dormenergy/floor2/room2/ack` |
| Floor 3 room 3 | `room3` | `dormenergy/floor3/room3/telemetry`, `.../status` | `dormenergy/floor3/room3/ack` |

The supplied broker ACL spells out each full topic. Gateway auth uses its own `gateway` username/password. MQTT 3.1.1, TLS verified CA/name, port 8883, stable persistent session, reconnect with bounded exponential delay and jitter, and QoS 1 are expected. No anonymous/wildcard permissions or cross-room identity. The broker binds sessions to usernames; one active ESP32 publisher per identity.

## Telemetry frame

```json
{
  "device_id": "ESP32-F1-R1",
  "unit_id": 4,
  "event_id": "meter-a-seq-000001",
  "meter_epoch": "physical-installation-a",
  "timestamp": "2026-10-12T04:15:30+00:00",
  "voltage": "230.400",
  "current": "0.9250",
  "power_w": "212.000",
  "energy_kwh": "12.350000",
  "connection_status": "connected",
  "measurement_origin": "hardware",
  "clock_synchronized": true
}
```

Replace identifiers/unit/time/numeric values with the registered mapping and actual original samples; do not send the example as a real reading. UTC ISO-8601 timezone required; numeric strings preserve precision. Maxima: 300 V (3 decimals), 100 A (4), 25,000 W (3), 1,000,000,000 cumulative kWh (6). Reject NaN/infinite/negative values, duplicate/unknown JSON fields, booleans as numbers, >8192-byte frames or >120-second future time when the gateway clock is trusted. Valid supply states are `connected`, `disconnected`, `protective_trip`, `unknown`; **reported supply state is distinct from MQTT connectivity**. Unknown cannot be changed to connected because the Pi is online.

`measurement_origin` and `clock_synchronized` are local protocol additions stripped before cloud ingestion. Local fixture origin is `development_fixture`, accepted only in development/isolated test modes and marked `gateway_fixture=true` at the test API. They are not evidence of real PZEM accuracy. The cloud endpoint remains `POST /api/iot/readings` with `X-Device-ID` and the per-device bearer API token from a Pi secret file.

## Durable publisher and application ACK

1. Establish valid sample time from a synchronized clock with a documented drift/holdover policy. If time or PZEM sampling fails, report uncertainty/diagnostics and preserve the cumulative counter; never invent timestamped consumption.
2. Read voltage/current/power/**actual cumulative kWh** and persist the immutable complete frame, stable event ID and meter epoch in an ESP32 durable journal before publishing. Event uniqueness must survive firmware/ESP32 restarts, e.g. a persistent installation UUID plus durable sequence.
3. Subscribe to the own ACK topic before sending the oldest unsent frame. Publish QoS 1 without retain. Retry with the **identical** content/event/time/epoch after outages. MQTT PUBACK proves transport reception by Mosquitto; it does not clear the ESP32 journal.
4. Clear/mark the original frame delivered locally only on the own authorized application's `stored` or `already_stored` ACK for the exact stable event ID. The gateway sends this after SQLite FULL commit. An ACK can be lost; replay is safe. Never repurpose an event ID for different values.
5. A `rejected` ACK is a durable diagnostic, **not** acceptance: retain/quarantine the original frame and alert the operator rather than busy-looping or manufacturing a replacement. Invalid frames have only a digest/category in the gateway diagnostics; firmware must retain the original for diagnosis. No successful ACK on storage failure/limit; retry after recovery.

```json
{"event_id":"meter-a-seq-000001","status":"stored"}
```

Equivalent duplicate ACK: `status=already_stored`. Rejection example: `{"event_id":"meter-a-seq-000001","status":"rejected","reason":"device_mapping_invalid"}`. MQTT delivery is at least once; the gateway/Flask dedupe prevents duplicate charge. Local ACK means safely queued on the Pi, **not** cloud acceptance or payment approval. Back up the Pi journal and use power/storage protection because an SD-card failure after ACK can still destroy local data.

Keep each room's backlog ordered; late packets following a synchronized newer packet can be held by Flask. The API receipt resolves uncertain already-accepted events by exact measurement comparison; a 409 alone does not prove an accepted reading.

## Connection status and actual reset

Publish QoS 1 retained status on connect and heartbeat at least every 30 seconds:

```json
{"device_id":"ESP32-F1-R1","unit_id":4,"online":true}
```

Set a QoS 1 retained LWT with the same mapping and `online=false`; publish offline before a graceful disconnect. Heartbeats update communication status only, never kWh/balance. A heartbeat with old telemetry does not mean current measurements are available. The gateway's health also requires fresh original sample time and an active service/broker before counting a room online.

An ESP32/Pi reboot is **not** a PZEM counter reset: keep its epoch and event journal. For an actual confirmed reset, use a new never-reused epoch, obtain local operator approval via `approve-reset`, and send the first new baseline with `reset_confirmed=true`. Retain that original until accepted. New baseline costs zero; subsequent measured increases are billed by Flask. Do not reset PZEM/epoch to work around a tariff/auth/network error.

## Future physical-control contract — design only

No command subscriber, relay driver, mains GPIO, automatic disconnection or physical command endpoint is implemented. `/api/iot/instructions` still returns no commands and `control_enabled=false`. A future design needs qualified electrical review, approved protective hardware, fail-safe/manual override and supervised trials before any actuation code.

A reviewed control protocol would need separately authorized/signed commands with immutable command ID, exact target, approved requested state, issuance/expiry and monotonic anti-replay checks; duplicate execution suppression; independent device state observation; result ACK distinguishing received/authorized/executed/failed; command expiry during outages; and a reviewed safe response to reboot, loss of clock/comms, or uncertain supply state. Broker topic access alone must not grant switching authority. Credit exhaustion, forecasts and a stale/offline meter must never synthesize a physical trip or automatically energize equipment. Actual contactor/protection/isolation design, installation, commissioning and applicable rules must be approved by a qualified practitioner and the thesis adviser before field work.
