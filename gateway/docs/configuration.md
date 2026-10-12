# Configuration, secrets and operational recovery

Copy an example into a private location and edit it. Paths are relative to the configuration file, not the current shell directory; absolute Pi paths are easiest. The gateway does not load the website `.env`. `python -m dormenergy_gateway --help` lists commands.

| Setting | Behavior |
| --- | --- |
| `mode` | Missing means `development`; other allowed modes are `isolated_test`, `real`. |
| `database` | Private SQLite path; one service owner. Mode/cloud origin/device mapping is bound to the journal. |
| `mqtt.host`, `port`, `client_id` | Pi LAN address/TLS port and stable identity; loopback plaintext is allowed only for local development/test. |
| `mqtt.username`, `password_file`, `ca_file` | Separate gateway broker credential, file-based secret, trusted LAN broker CA. TLS mandatory in real mode and on non-loopback. |
| `devices` | Unique alias/device/unit and distinct exact telemetry/ACK/status topics; initial physical meter epoch; per-device API token file for uploading. |
| `cloud.enabled` | Default false. Development mode rejects true. Isolated mode accepts only an explicit loopback API; real mode requires HTTPS. |
| `cloud.base_url`, `ca_file` | Canonical origin without path/query/credentials; normal system CA trust unless a private test CA is configured. |
| `cloud.timeout_seconds`, `max_attempts` | Default 10 seconds/8 attempts. Ranges 1–30 seconds/1–20 attempts. Retry delay is capped at 300 seconds plus ≤20% jitter. |
| `storage.max_pending` | Default 100,000 unsent rows; new input is refused, existing unsent rows retained. |
| `storage.max_bytes` | Default 512 MiB database+WAL admission cutoff; page expansion can exceed the cutoff. Keep extra disk reserve. |
| `storage.min_free_bytes` | Default 128 MiB reserve before admitting a frame. |
| `storage.retention_days` | Default 7 days for confirmed sent payload details; compact receipts remain. |
| `stale_seconds` | Default 90 seconds for original measurement and status heartbeat freshness. Firmware heartbeat should be more frequent. |
| `ntp_grace_seconds` | 0–86400 seconds, default 24 hours, after verified Pi synchronization; set 0 to require current NTP status. No holdover is assumed on startup. |

A non-transmitting development queue cannot be switched into real mode; preserve it and select a **new database**. Changing destination or mapping also requires a separate journal and deliberate reconciliation of old unsent data. Rotating token/password files preserves mapping and queued frames. `DEV-` identities and fixture-origin frames are forbidden in real mode. Isolated fixtures carry `gateway_fixture=true`, which the production backend rejects. Never point tests at your real database or use live device tokens in fixtures.

## Provision and rotate credentials

The website generates one API token per registered room in Device monitoring after owner-password verification. Store each one directly in its own Pi secret file. MQTT room passwords are different credentials used only between each ESP32 and Mosquitto. Keep cloud tokens on the Pi; the ESP32 needs its own broker password and CA.

```bash
sudo -u dormenergy-gateway /opt/dormenergy/THESIS-/gateway/.venv/bin/dormenergy-gateway secret --destination /etc/dormenergy-gateway/secrets/room1-api
```

The value is entered at a hidden prompt, never in argv, Git or logs. Secret files must be mode 0600 or stricter on POSIX; protect the configuration/secrets directory too. Windows development needs a private user directory/NTFS permissions. The command refuses overwrites. For rotation, create a new file, update the JSON path, provision the matching website/broker token and restart the service. Authenticate with the new credential before resuming that room. A token revoked on the website should yield a held 401 until explicitly repaired; do not reset meter epochs or counters to solve authentication.

## Inspect and resume

```bash
sudo systemctl status dormenergy-gateway --no-pager
sudo journalctl -u dormenergy-gateway -n 60 --no-pager
sudo -u dormenergy-gateway /opt/dormenergy/THESIS-/gateway/.venv/bin/dormenergy-gateway health --config /etc/dormenergy-gateway/config.json
sudo -u dormenergy-gateway /opt/dormenergy/THESIS-/gateway/.venv/bin/dormenergy-gateway resume --config /etc/dormenergy-gateway/config.json --device room1
```

Health distinguishes service heartbeat, broker connection, status freshness, **original sample freshness**, queue states, last synchronized time and transport/cloud health. `cloud_path_reachable` is evidence of the configured path, not a diagnosis of global Internet access. DNS/TLS/socket failures remain `request_failed`. A 401/422 can have a reachable path while the room is held. Null measurements/status are unknown, not zero/offline measurements. Do not share unredacted config/env screenshots.

Resume only after resolving the held reason. A missing/expired tariff needs authoritative utility reconciliation in Flask; do not backdate/alter samples to make a charge pass. Out-of-order events need an operator to compare server receipts and retained originals; the package does not rewrite financial history or silently discard those rows. `blocked_clock` rows can be resumed after clock correction, keeping their original timestamps.

For an actual physical counter reset, choose a **new**, never reused epoch, document the physical operation and approve it locally:

```bash
sudo -u dormenergy-gateway /opt/dormenergy/THESIS-/gateway/.venv/bin/dormenergy-gateway approve-reset --config /etc/dormenergy-gateway/config.json --device room1 --epoch physical-reset-2026-11-01
```

Then configure ESP32 firmware to retain that new epoch and send `reset_confirmed=true` on the first new baseline. A rejected reset remains in the **ESP32** journal; approval lets the identical original reset frame be replayed. Ordinary reboots retain epochs. The backend records the reset and bills a zero-cost new baseline; energy in an unmeasured gap is not guessed.

## Backup, restore and update

Use SQLite's backup API while running; copying only the live `.sqlite3` file can omit WAL data. Protect backups and separately back up secret files through your secure storage process.

```bash
sudo -u dormenergy-gateway /opt/dormenergy/THESIS-/gateway/.venv/bin/dormenergy-gateway backup --config /etc/dormenergy-gateway/config.json --destination /var/lib/dormenergy-gateway/backup-2026-11-01.sqlite3
sudo -u dormenergy-gateway /opt/dormenergy/THESIS-/gateway/.venv/bin/dormenergy-gateway restore-check --backup /var/lib/dormenergy-gateway/backup-2026-11-01.sqlite3
```

Restore **only while the service is stopped**, after integrity verification and confirming the profile/device mapping. Preserve the current database, WAL and SHM together in a recovery directory first. Copy the verified backup to the configured database path; set owner/mode 0600; ensure old WAL/SHM are moved away. Start the service and inspect health. A restored older pending row is resolved against the cloud receipt; restore must not trigger new billing for an accepted event. Never clear a corrupt database and start a fake new baseline automatically. For SD-card failure, retain the card/ESP32 journals and use the last verified backup; unacknowledged firmware frames must replay.

For an update, back up, stop the service, preserve the installed release/venv for rollback, fetch a **reviewed commit**, install with pinned dependencies into a replacement venv, and run unit checks before switching the systemd executable. Restart, confirm health and receipt-based synchronization. Do not `git pull` an unreviewed branch into an occupied installation. JSON logs contain fixed category strings and configured room aliases, not payloads/tokens. Configure journald retention and encrypted backup off the SD card; budget storage for retained compact receipts and outages.
