#!/usr/bin/env python3
"""Generate embedded arrays from exact pinned assets; never rewrite the payload."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN = 'e9cb5f84428910e83c0c43957fa8deb01ce4fc3d7b899aaecea7810993c62200'


def generate(script, output):
    inputs = {'payload_script': script, 'runner_script': ROOT / 'bin/root-control',
              'boot_script': ROOT / 'scripts/autostart.sh'}
    metadata = {}
    with output.open('w') as stream:
        stream.write('/* Generated from pinned original bytes. */\n')
        for name, path in inputs.items():
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if name == 'payload_script' and digest != PIN:
                raise SystemExit('1_no_login.sh hash differs from the approved baseline')
            metadata[name] = {'sha256': digest, 'bytes': len(data)}
            stream.write(f'static const unsigned char {name}[] = {{\n')
            for start in range(0, len(data), 24):
                stream.write(','.join(f'0x{v:02x}' for v in data[start:start + 24]) + ',\n')
            stream.write(f'}};\nstatic const size_t {name}_len = sizeof({name});\n')
    output.with_suffix('.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print(f'Pinned assets embedded: {output.absolute()}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--script', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    generate(args.script, args.output)
