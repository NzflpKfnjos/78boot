#!/usr/bin/env python3
"""Embed the fixed boot-preparation executable into the signed LKM."""
import argparse
import hashlib
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--bootstrap', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
data = args.bootstrap.read_bytes()
if data[:6] != b'\x7fELF\x02\x01' or int.from_bytes(data[18:20], 'little') != 183:
    raise SystemExit('bootstrap must be an Android AArch64 ELF64 executable')
args.output.parent.mkdir(parents=True, exist_ok=True)
with args.output.open('w') as stream:
    stream.write('/* Exact boot executable; protected by the signed init_boot image. */\n')
    stream.write('static const unsigned char root_bootstrap_blob[] = {\n')
    for start in range(0, len(data), 24):
        stream.write(','.join(f'0x{v:02x}' for v in data[start:start + 24]) + ',\n')
    stream.write('};\n')
print(f'Embedded bootstrap: bytes={len(data)} sha256={hashlib.sha256(data).hexdigest()}')
