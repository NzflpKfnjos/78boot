#!/usr/bin/env python3
"""Verify the small firmware inputs before any Actions patching."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
lock = json.loads((ROOT / 'config/upstreams.lock.json').read_text())
for name, expected in lock['stock_inputs'].items():
    path = ROOT / 'ci/stock' / name
    with path.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != expected:
        raise SystemExit(f'stock hash mismatch: {path}')
    print(f'{name}: {actual}')
