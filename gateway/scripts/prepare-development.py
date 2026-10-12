"""Create a private loopback broker profile and ephemeral development credentials.

Run from repo root. No production tokens are needed or copied. Requires Mosquitto's
password utility. Existing folders are refused, preserving old queues/fixtures.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('--directory', default='gateway-local')
parser.add_argument('--mosquitto-passwd', default=shutil.which('mosquitto_passwd'))
args = parser.parse_args()
if not args.mosquitto_passwd:
    parser.error('Install Mosquitto and put mosquitto_passwd on PATH or pass its executable path.')
root = Path(args.directory).resolve()
if root.exists():
    parser.error('Choose a new directory; existing fixtures and journals will not be overwritten.')
root.mkdir(mode=0o700, parents=True)
credentials = {user: secrets.token_urlsafe(32) for user in ('gateway', 'room1', 'room2', 'room3')}
secret_dir = root/'secrets'; secret_dir.mkdir(mode=0o700)
for user, value in credentials.items():
    path = secret_dir/(user+'-mqtt'); path.write_text(value+'\n'); path.chmod(0o600)
passwords = root/'broker.passwd'
passwords.write_text(''.join(f'{user}:{value}\n' for user, value in credentials.items())); passwords.chmod(0o600)
result = subprocess.run([args.mosquitto_passwd, '-U', str(passwords)], capture_output=True, check=False)
if result.returncode:
    passwords.unlink(missing_ok=True)
    parser.error('The password file could not be hashed. Remove this incomplete private folder before retrying.')
repo = Path(__file__).resolve().parents[2]
shutil.copyfile(repo/'gateway/mosquitto/acl.example', root/'broker.acl')
config = json.loads((repo/'gateway/config/development.example.json').read_text())
(root/'config.json').write_text(json.dumps(config, indent=2)+'\n')
(root/'broker.conf').write_text('\n'.join([
    'listener 18883 127.0.0.1', 'allow_anonymous false', 'use_username_as_clientid true',
    'password_file '+passwords.as_posix(), 'acl_file '+(root/'broker.acl').as_posix(),
    'persistence true', 'persistence_location '+root.as_posix()+'/', 'autosave_interval 30',
    *(['user root'] if os.name != 'nt' and os.getuid() == 0 else [])])+ '\n')
print('Private development files prepared. Start the loopback broker, gateway, and three separate fixture publishers. Cloud upload is disabled.')
