"""Read-only PyPI release advisory check for the current platform's exact pins."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import urllib.request
from packaging.requirements import Requirement


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='artifacts/dependency-advisories.json')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    requirements = set()
    for filename in ('requirements.lock.txt', 'requirements-browser.txt', 'gateway/requirements.txt', 'gateway/requirements-dev.txt'):
        for line in (root/filename).read_text().splitlines():
            if not line.strip() or line.startswith(('#', '-r')): continue
            requirement = Requirement(line)
            if requirement.marker and not requirement.marker.evaluate(): continue
            pins = list(requirement.specifier)
            if len(pins) != 1 or pins[0].operator != '==':
                raise ValueError('The advisory checker requires exact dependency pins.')
            requirements.add((requirement.name, pins[0].version))
    def check(pin):
        name, version = pin
        try:
            with urllib.request.urlopen(f'https://pypi.org/pypi/{name}/{version}/json', timeout=20) as response:
                metadata = json.load(response)
            return dict(name=name, version=version, checked=True,
                advisories=[dict(id=a['id'], aliases=a.get('aliases', []), fixed_in=a.get('fixed_in', []),
                                 link=a.get('link')) for a in metadata.get('vulnerabilities', [])])
        except (OSError, ValueError, KeyError):
            return dict(name=name, version=version, checked=False, advisories=[])
    with ThreadPoolExecutor(max_workers=6) as pool: results = list(pool.map(check, sorted(requirements)))
    report = dict(checked_at=datetime.now(timezone.utc).isoformat(), source='https://pypi.org/pypi/{package}/{version}/json',
        scope='Published release advisories for exact active-platform pins; not an OS scan or penetration test.', dependencies=results)
    target = Path(args.output); target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2)+'\n')
    failed = sum(not r['checked'] for r in results)
    affected = sum(bool(r['advisories']) for r in results)
    print(json.dumps(dict(packages=len(results), unavailable=failed, packages_with_advisories=affected)))
    raise SystemExit(1 if failed or affected else 0)


if __name__ == '__main__': main()
