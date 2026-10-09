#!/usr/bin/env python3
"""Collect read-only Android facts over adb and preserve per-command evidence."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = [
    ['getprop', 'ro.product.model'], ['getprop', 'ro.product.device'],
    ['getprop', 'ro.build.fingerprint'], ['getprop', 'ro.build.version.release'],
    ['getprop', 'ro.build.version.sdk'], ['getprop', 'ro.product.cpu.abilist'],
    ['getprop', 'ro.boot.slot_suffix'], ['getprop', 'ro.boot.flash.locked'],
    ['getprop', 'ro.boot.verifiedbootstate'], ['uname', '-a'], ['id'], ['getenforce'],
    ['cat', '/proc/self/attr/current'], ['cat', '/proc/self/status'],
    ['ls', '-l', '/dev/block/by-name/boot_a', '/dev/block/by-name/boot_b',
     '/dev/block/by-name/init_boot_a', '/dev/block/by-name/init_boot_b',
     '/dev/block/by-name/vbmeta_a', '/dev/block/by-name/vbmeta_b'],
    ['ls', '-ld', '/data/adb'], ['ls', '-l', '/proc/config.gz'],
]


def collect(adb, serial=None):
    records = []

    def run(args):
        try:
            result = subprocess.run([adb, *args], capture_output=True, text=True, timeout=30)
            record = {'command': [adb, *args], 'input': None, 'stdout': result.stdout,
                      'stderr': result.stderr, 'exit_status': result.returncode}
        except subprocess.TimeoutExpired as exc:
            def text(value):
                return value.decode(errors='replace') if isinstance(value, bytes) else (value or '')
            record = {'command': [adb, *args], 'input': None, 'stdout': text(exc.stdout),
                      'stderr': text(exc.stderr), 'exit_status': None, 'error': 'timeout'}
        records.append(record)
        return record

    devices = run(['devices', '-l'])
    if devices['exit_status'] != 0:
        return {'state': 'adb-error', 'records': records}, 3
    ready = [line.split()[0] for line in devices['stdout'].splitlines()[1:]
             if len(line.split()) >= 2 and line.split()[1] == 'device']
    if (serial and serial not in ready) or (not serial and len(ready) != 1):
        state = 'multiple-devices-select-serial' if len(ready) > 1 and not serial else 'no-authorized-device'
        return {'state': state, 'records': records}, 3
    selected = serial or ready[0]
    for command in COMMANDS:
        run(['-s', selected, 'shell', *command])
    return {'state': 'collected', 'serial': selected, 'root_requested': False,
            'records': records, 'note': 'Permission errors are evidence; this collector never invokes su.'}, 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial')
    parser.add_argument('--output', type=Path, default=ROOT / 'verification/stage0/device.json')
    args = parser.parse_args()
    adb = shutil.which('adb')
    if not adb:
        result, status = {'state': 'adb-not-found', 'records': []}, 3
    else:
        result, status = collect(adb, args.serial)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(f'{result["state"]}: {args.output.absolute()}')
    return status


if __name__ == '__main__':
    sys.exit(main())
