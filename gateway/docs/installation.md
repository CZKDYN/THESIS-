# Installation: Windows testing to Raspberry Pi OS Lite

All commands below start in the repository root unless stated otherwise. Tests need no production secrets. Never run fixture publishers with real configuration or reuse a development journal for a real installation.

## Windows: Python and three local mock devices

Install Python 3.12 from python.org and Mosquitto 2.x from [the official download page](https://mosquitto.org/download/). The following examples use its default `C:\Program Files\mosquitto` installation directory; adjust if necessary. Do not expose the development listener beyond loopback.

```powershell
git clone https://github.com/CZKDYN/THESIS-.git
cd THESIS-
git switch feature/raspberry-pi-gateway
py -3.12 -m venv gateway/.venv
.\gateway\.venv\Scripts\Activate.ps1
python -m pip install -r gateway/requirements-dev.txt
python -m pip install --no-build-isolation --no-deps -e gateway
.\gateway\scripts\run-tests.ps1
python gateway/scripts/prepare-development.py --mosquitto-passwd 'C:\Program Files\mosquitto\mosquitto_passwd.exe'
& 'C:\Program Files\mosquitto\mosquitto.exe' -c "$PWD/gateway-local/broker.conf"
```

Leave that broker terminal running. In a second terminal, activate the same venv, then:

```powershell
dormenergy-gateway run --config gateway-local/config.json
```

In separate terminals run each independent room publisher (same repo/venv):

```powershell
dormenergy-gateway simulate --config gateway-local/config.json --device room1 --username room1 --password-file gateway-local/secrets/room1-mqtt --journal gateway-local/room1.sqlite3 --scenario duplicate
dormenergy-gateway simulate --config gateway-local/config.json --device room2 --username room2 --password-file gateway-local/secrets/room2-mqtt --journal gateway-local/room2.sqlite3 --scenario delayed
dormenergy-gateway simulate --config gateway-local/config.json --device room3 --username room3 --password-file gateway-local/secrets/room3-mqtt --journal gateway-local/room3.sqlite3 --scenario disconnect
dormenergy-gateway health --config gateway-local/config.json
```

`development` prints queue/health diagnostics and leaves fixtures unsent; it does not insert data into Render. Successful application ACK counts prove local commits, not cloud delivery. If the publisher exits with retained frames, rerun with the **same** publisher journal: original IDs/times/counters are replayed. `out-of-order` deliberately reverses local frames; use an entirely fresh isolated profile for that scenario, because a late reading after a cloud acceptance will be held rather than rewriting history. For a reset fixture, first approve epoch `installation-1-reset` for that alias; then use `--scenario reset`. A fresh publisher journal is needed when changing its scenario; retain old unsent fixture files.

No administrator account is created on your hosted site by these commands. To see mock readings in an actual test dashboard, use the next isolated network test; do not enable the hosted simulator.

## Isolated PostgreSQL/dashboard test (Windows or Linux Docker)

Install Docker Desktop and select Linux containers. These commands build a test runner and start an expendable PostgreSQL 17 service, with **no host ports** and no production `.env`, credentials, database volume or cloud target. The test creates a local TLS MQTT broker and verified HTTPS Flask API inside the runner, publishes three mock rooms, checks outages/restarts/receipt reconciliation, and checks admin/three tenant dashboard routes and scopes.

```powershell
docker compose -f gateway/compose.test.yml up --build --abort-on-container-exit --exit-code-from tests
$testExit = $LASTEXITCODE
docker compose -f gateway/compose.test.yml down --volumes
if ($testExit -ne 0) { throw "Gateway tests failed with exit code $testExit" }
```

The fixture database is explicitly `dormenergy_gateway_test`; tests drop its tables on completion. Never substitute an occupied/local development database, even if its name ends in `_test`. Docker images and broker/Python OS packages require Internet access during the build; running the test then requires only the internal network. Linux native commands and optional real Chromium rendering are in [testing.md](testing.md). Windows and ARM hardware execution are pending physical/platform validation.

## Raspberry Pi OS Lite: gateway installation

Use current supported **64-bit Raspberry Pi OS Lite**, Python 3.11 or later. Provision SSH access, apply OS updates, set a reserved LAN IP (example `192.168.1.20`) and prefer Ethernet. Install the **reviewed** gateway/backend release; the feature branch below is for evaluation until reviewed/merged. No production service is activated by a GitHub push.

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv git mosquitto mosquitto-clients openssl
python3 --version
sudo timedatectl set-ntp true
timedatectl show --property=NTPSynchronized --value
sudo useradd --system --home-dir /var/lib/dormenergy-gateway --shell /usr/sbin/nologin dormenergy-gateway
sudo install -d -m 0755 /opt/dormenergy
sudo git clone --branch feature/raspberry-pi-gateway https://github.com/CZKDYN/THESIS-.git /opt/dormenergy/THESIS-
cd /opt/dormenergy/THESIS-
sudo python3 -m venv gateway/.venv
sudo gateway/.venv/bin/python -m pip install -r gateway/requirements-dev.txt
sudo gateway/.venv/bin/python -m pip install --no-build-isolation --no-deps ./gateway
gateway/.venv/bin/python -m pytest -q gateway/tests/test_gateway.py
sudo install -d -o root -g dormenergy-gateway -m 0750 /etc/dormenergy-gateway
sudo install -d -o dormenergy-gateway -g dormenergy-gateway -m 0700 /etc/dormenergy-gateway/secrets
sudo install -d -o dormenergy-gateway -g dormenergy-gateway -m 0700 /var/lib/dormenergy-gateway
sudo install -o root -g dormenergy-gateway -m 0640 gateway/config/real.example.json /etc/dormenergy-gateway/config.json
```

`useradd` and clone are first-install operations: reuse an existing reviewed installation rather than overwriting it. For a production-only venv, install `gateway/requirements.txt` and then `pip install ./gateway` with its pinned isolated build dependencies; the dev tooling above enables local checks without Flask/XGBoost on the Pi. Python packages use verified TLS. The Pi does not need PostgreSQL, Node.js, a browser or the website's large ML dependencies.

## Broker TLS certificates

Use a private LAN CA or an organization-issued server certificate. The following reproducible example creates an **encrypted CA private key on the administrator's Linux workstation**, plus a Pi server certificate. Store the directory outside Git. Substitute the actual reserved IP and DNS name in `subjectAltName`; changing the hostname/IP later requires a matching new certificate.

```bash
umask 077
mkdir -p ~/dormenergy-pki
cd ~/dormenergy-pki
openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -keyout ca.key -out ca.crt -subj '/CN=DormEnergy Local MQTT CA'
openssl req -new -newkey rsa:2048 -nodes -keyout pi.key -out pi.csr -subj '/CN=dormenergy-pi.local'
printf '%s\n' 'subjectAltName=IP:192.168.1.20,DNS:dormenergy-pi.local' 'extendedKeyUsage=serverAuth' 'basicConstraints=CA:FALSE' > pi.ext
openssl x509 -req -in pi.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out pi.crt -days 365 -sha256 -extfile pi.ext
openssl verify -CAfile ca.crt pi.crt
openssl x509 -in pi.crt -noout -subject -dates
```

OpenSSL prompts for the CA key passphrase; never put it in argv. Transfer only `pi.key`, `pi.crt` and public `ca.crt` to a private staging folder on the Pi via authenticated SSH/SCP. Keep `ca.key` off the Pi. Copy the public CA into each ESP32's trusted store; firmware must verify the broker name/IP and certificate validity. Plan certificate renewal and clock synchronization before expiry.

On the Pi, assuming those files were securely transferred to `~/dormenergy-pki/`:

```bash
sudo install -d -o root -g mosquitto -m 0750 /etc/mosquitto/certs
sudo install -o root -g mosquitto -m 0640 ~/dormenergy-pki/pi.key /etc/mosquitto/certs/pi.key
sudo install -o root -g mosquitto -m 0644 ~/dormenergy-pki/pi.crt /etc/mosquitto/certs/pi.crt
sudo install -o root -g dormenergy-gateway -m 0644 ~/dormenergy-pki/ca.crt /etc/dormenergy-gateway/ca.crt
cd /opt/dormenergy/THESIS-
sudo install -o root -g mosquitto -m 0640 gateway/mosquitto/acl.example /etc/mosquitto/dormenergy.acl
sudo install -o root -g root -m 0644 gateway/mosquitto/production.conf.example /etc/mosquitto/conf.d/dormenergy.conf
sudoedit /etc/mosquitto/conf.d/dormenergy.conf
```

Set listener IP to the real reserved Pi address. Remove conflicting listeners or anonymous/plaintext LAN listeners; the packaged main config should keep persistence enabled. Do not disable TLS verification to work around a SAN or expiry error.

## Broker passwords, cloud tokens and service

Generate four distinct strong broker passwords in a password manager. `mosquitto_passwd` prompts privately; reuse each correct value only for its paired subscriber/ESP32. The website API tokens are **separate** credentials. These commands use no plaintext command-line password:

```bash
sudo mosquitto_passwd -c /etc/mosquitto/dormenergy.passwd gateway
sudo mosquitto_passwd /etc/mosquitto/dormenergy.passwd room1
sudo mosquitto_passwd /etc/mosquitto/dormenergy.passwd room2
sudo mosquitto_passwd /etc/mosquitto/dormenergy.passwd room3
sudo chown root:mosquitto /etc/mosquitto/dormenergy.passwd
sudo chmod 0640 /etc/mosquitto/dormenergy.passwd
sudo -u dormenergy-gateway gateway/.venv/bin/dormenergy-gateway secret --destination /etc/dormenergy-gateway/secrets/gateway-mqtt
sudo -u dormenergy-gateway gateway/.venv/bin/dormenergy-gateway secret --destination /etc/dormenergy-gateway/secrets/room1-api
sudo -u dormenergy-gateway gateway/.venv/bin/dormenergy-gateway secret --destination /etc/dormenergy-gateway/secrets/room2-api
sudo -u dormenergy-gateway gateway/.venv/bin/dormenergy-gateway secret --destination /etc/dormenergy-gateway/secrets/room3-api
sudoedit /etc/dormenergy-gateway/config.json
```

Enter the same MQTT `gateway` password in its secret prompt. Generate the three cloud tokens through website Device monitoring, after registering actual units, and enter each only into its paired API file. Edit the JSON's **registered device IDs and actual unit IDs**, Pi address/CA, topics and initial physical epochs; sample unit IDs `1–3` are placeholders. Do not use retired demo IDs. Keep `mode=real`, `cloud.enabled=true`, and the website `SIMULATION_MODE=false`. Confirm the applicable utility source/period/validity in the website hosting configuration first; the gateway does not turn the owner-supplied PHP 14.74 reference into a billable tariff.

Provision ESP32 room1/room2/room3 with only their respective MQTT password, topics, physical mapping, public CA, NTP and durable-journal firmware. Keep API tokens on the Pi. Restrict TCP 8883 to the dormitory private LAN in the existing firewall, retain authorized SSH access, and configure no router forwarding/public listener. Only outbound HTTPS to the configured cloud is required.

```bash
sudo systemctl enable --now mosquitto
sudo systemctl restart mosquitto
sudo journalctl -u mosquitto -n 40 --no-pager
sudo install -o root -g root -m 0644 gateway/systemd/dormenergy-gateway.service /etc/systemd/system/dormenergy-gateway.service
sudo systemd-analyze verify /etc/systemd/system/dormenergy-gateway.service
sudo systemctl daemon-reload
sudo systemctl enable --now dormenergy-gateway
sudo systemctl status dormenergy-gateway --no-pager
sudo journalctl -u dormenergy-gateway -f
```

The service waits on networking, runs without root/GPIO privileges, writes only its state directory, and restarts after an actual worker failure. NTP readiness is checked in the application before uploads, not assumed from systemd target activation. Check health and the first actual baseline/receipt before adding rooms 2/3. Stop mock publishers and use a fresh real journal. See the [hardware integration checklist](troubleshooting.md); no field deployment or live billing is claimed until those checks are completed.
