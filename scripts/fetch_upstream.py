#!/usr/bin/env python3
"""Fetch a reviewed upstream commit; never substitute the moving branch tip."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def fetch(name):
    lock = json.loads((ROOT / 'config/upstreams.lock.json').read_text())
    source = lock['sources'][name]
    path = ROOT / 'upstream' / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['git', 'init', str(path)], check=True)
        subprocess.run(['git', '-C', str(path), 'remote', 'add', 'origin', source['url']], check=True)
        subprocess.run(['git', '-C', str(path), 'fetch', '--depth', '1', 'origin', source['commit']], check=True)
        subprocess.run(['git', '-C', str(path), 'checkout', '--detach', source['commit']], check=True)
    actual = subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != source['commit']:
        raise SystemExit(f'{name}: checkout differs from lock: {actual}')
    dirty = subprocess.check_output(['git', '-C', str(path), 'status', '--porcelain'], text=True)
    if dirty:
        raise SystemExit(f'{name}: source checkout contains changes')
    print(f'{name}: {actual} at {path}')


if __name__ == '__main__':
    fetch(sys.argv[1] if len(sys.argv) == 2 else 'KernelSU')
