"""Fetch the pinned, integrity-checked test-only axe script; never serve it in Flask."""
import argparse
import base64
import hashlib
import io
from pathlib import Path
import tarfile
import urllib.request

VERSION = '4.14.0'
SHA512 = '9WTZxEjsZ7b13TH8JPmbV2z8CHbl80/2hm3XPEG4JgNdQLK81IBRXmSxHfMAOkSqQeRxT/0dwNDz2GOm3zzpcQ=='


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='artifacts/website/axe.min.js')
    args = parser.parse_args()
    url = f'https://registry.npmjs.org/axe-core/-/axe-core-{VERSION}.tgz'
    with urllib.request.urlopen(url, timeout=30) as response:
        if response.geturl() != url:
            raise RuntimeError('Unexpected registry redirect.')
        archive = response.read(8 * 1024 * 1024 + 1)
    if len(archive) > 8 * 1024 * 1024 or base64.b64encode(hashlib.sha512(archive).digest()).decode() != SHA512:
        raise RuntimeError('axe-core archive failed its pinned integrity check.')
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as package:
        member = package.getmember('package/axe.min.js')
        if not member.isfile() or member.size > 4 * 1024 * 1024:
            raise RuntimeError('Unexpected axe script archive member.')
        script = package.extractfile(member).read()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(script)
    print(f'Verified axe-core {VERSION}; test-only script saved to {output}.')


if __name__ == '__main__':
    main()
