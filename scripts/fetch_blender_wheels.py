"""Fetch a pinned Blender build wheelhouse via PyPI JSON when simple-index lookup stalls."""
from __future__ import annotations
import hashlib
import importlib.metadata as installed
import json
from pathlib import Path
import urllib.request
from packaging.requirements import Requirement
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'build' / 'blender-wheels'
TAGS = {tag: index for index, tag in enumerate(sys_tags())}
PINS = {}
for line in (ROOT / 'packaging' / 'blender-requirements.txt').read_text('utf-8').splitlines():
    if line.strip() and not line.startswith('#'):
        req = Requirement(line)
        PINS[canonicalize_name(req.name)] = str(next(iter(req.specifier)).version)


def fetch(url):
    import time
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                return response.read()
        except (OSError, TimeoutError):
            if attempt == 2:
                raise
            print('Retrying build download: ' + url, flush=True)
            time.sleep(1)


def info(name, version=None):
    path = f'{name}/{version}/json' if version else f'{name}/json'
    return json.loads(fetch('https://pypi.org/pypi/' + path))


def best_wheel(data):
    ranked = []
    for file in data['urls']:
        if not file['filename'].endswith('.whl') or file.get('yanked'):
            continue
        try:
            _, _, _, tags = parse_wheel_filename(file['filename'])
            score = min((TAGS[tag] for tag in tags if tag in TAGS), default=None)
            if score is not None:
                ranked.append((score, file))
        except ValueError:
            pass
    return min(ranked, key=lambda item: item[0])[1] if ranked else None


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    pending = [Requirement(f'{name}=={version}') for name, version in PINS.items()]
    selected, constraints, extras_seen = {}, {}, {}
    while pending:
        req = pending.pop(0)
        name = canonicalize_name(req.name)
        constraints.setdefault(name, []).append(req.specifier)
        if name in selected:
            data = selected[name]
            if not all(data['info']['version'] in spec for spec in constraints[name]):
                raise RuntimeError(f'Conflicting wheel constraints for {name}; update the isolated build pins')
        else:
            preferred = PINS.get(name)
            if not preferred:
                try:
                    preferred = installed.version(name)
                except installed.PackageNotFoundError:
                    preferred = None
            if preferred and not all(preferred in spec for spec in constraints[name]):
                preferred = None
            data = info(name, preferred)
            version = data['info']['version']
            if not all(version in spec for spec in constraints[name]) or not best_wheel(data):
                candidates = sorted((Version(v) for v in data.get('releases', {}) if not Version(v).is_prerelease), reverse=True)
                for candidate in candidates:
                    if all(str(candidate) in spec for spec in constraints[name]):
                        option = info(name, str(candidate))
                        if best_wheel(option):
                            data = option
                            break
                else:
                    raise RuntimeError('No compatible pinned wheel for ' + name)
            selected[name] = data
            wheel = best_wheel(data)
            if wheel is None:
                raise RuntimeError('Missing wheel: ' + name)
            target = DEST / wheel['filename']
            expected = wheel['digests']['sha256']
            if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                raw = fetch(wheel['url'])
                if hashlib.sha256(raw).hexdigest() != expected:
                    raise RuntimeError('Wheel checksum mismatch: ' + name)
                target.write_bytes(raw)
            print(f"Verified {name}=={data['info']['version']}", flush=True)
        extra_set = set(req.extras) | {''}
        new_extras = extra_set - extras_seen.setdefault(name, set())
        extras_seen[name].update(extra_set)
        if not new_extras:
            continue
        for declaration in data['info'].get('requires_dist') or []:
            child = Requirement(declaration)
            if child.marker is None or any(child.marker.evaluate({'extra': extra}) for extra in new_extras):
                pending.append(child)
    lock = '\n'.join(f"{name}=={data['info']['version']}" for name, data in sorted(selected.items())) + '\n'
    (DEST / 'resolved.txt').write_text(lock, 'utf-8')
    print('Wheelhouse ready; install with --no-index --find-links.', flush=True)


if __name__ == '__main__':
    main()
