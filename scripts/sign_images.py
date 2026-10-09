#!/usr/bin/env python3
"""Rebuild AVB with the user's tool, verify, then publish a fresh output directory.

boot with an independent signature: boot.img.
init_boot with a hash footer: init_boot.img AND its rebuilt vbmeta.img.
This performs host validation only; it neither patches root nor flashes a device.
"""
import argparse
from collections import Counter
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from avbtool_host import PINS, ROOT, SUPPLIED, load_avbtool, load_supplied


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def parse(avb, path):
    image = avb.ImageHandler(str(path), read_only=True)
    footer, header, descriptors, _ = avb.Avb()._parse_image(image)
    image.seek(footer.vbmeta_offset if footer else 0)
    blob = image.read(header.SIZE + header.authentication_data_block_size
                      + header.auxiliary_data_block_size)
    offset = header.SIZE + header.authentication_data_block_size + header.public_key_offset
    key = bytes(blob[offset:offset + header.public_key_size])
    return footer, header, descriptors, blob, key


def summary(avb, parsed):
    footer, header, descriptors, _, key = parsed
    return {
        'algorithm': avb.lookup_algorithm_by_type(header.algorithm_type)[0],
        'rollback_index': header.rollback_index,
        'rollback_index_location': header.rollback_index_location,
        'flags': header.flags,
        'public_key_sha1': hashlib.sha1(key).hexdigest() if key else None,
        'original_image_size': footer.original_image_size if footer else None,
        'hashes': {d.partition_name: {'digest': d.digest.hex(), 'salt': d.salt.hex(),
                                     'image_size': d.image_size}
                   for d in descriptors if isinstance(d, avb.AvbHashDescriptor)},
    }


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sign(inputs, vbmeta, output):
    require(not output.exists() and not output.is_symlink(), 'output already exists')
    for path in [*inputs.values(), *([vbmeta] if vbmeta else [])]:
        require(path.is_file() and not path.is_symlink(), f'not a regular image: {path}')
    avb = load_avbtool()
    rebuild = load_supplied('rebuild_avb.py', 'supplied_rebuild_avb')
    for relative, expected in PINS.items():
        require(sha256(SUPPLIED / relative) == expected, f'tool hash mismatch: {relative}')
    before = {name: parse(avb, path) for name, path in inputs.items()}
    ordinary = []
    for name, path in inputs.items():
        footer, header, descriptors, _, key = before[name]
        with path.open('rb') as stream:
            require(stream.read(8) == b'ANDROID!', f'{name}: Android boot magic required')
        require(footer is not None, f'{name}: AVB footer missing; retain the original footer when patching')
        hashes = [d for d in descriptors if isinstance(d, avb.AvbHashDescriptor)]
        require(len(hashes) == 1 and hashes[0].partition_name == name,
                f'{name}: partition descriptor mismatch')
        require(hashes[0].hash_algorithm == 'sha256' and hashes[0].flags == 0,
                f'{name}: unsupported hash algorithm or flags')
        require(header.flags == 0 and header.rollback_index_location == 0,
                f'{name}: unsupported AVB flags or rollback index location')
        require(all(isinstance(d, (avb.AvbHashDescriptor, avb.AvbPropertyDescriptor))
                    for d in descriptors), f'{name}: unsupported descriptors')
        algorithm = summary(avb, before[name])['algorithm']
        if algorithm == 'NONE':
            require(header.rollback_index == 0, f'{name}: unsupported unsigned rollback index')
            ordinary.append(name)
        else:
            require(algorithm in ('SHA256_RSA2048', 'SHA256_RSA4096',
                                  'SHA512_RSA2048', 'SHA512_RSA4096'),
                    f'{name}: unsupported algorithm {algorithm}')
            bits = algorithm.rsplit('RSA', 1)[1]
            require(avb.RSAPublicKey(str(SUPPLIED / f'tools/pem/testkey_rsa{bits}.pem')).encode() == key,
                    f'{name}: supplied signing key does not match the input public key')
    require(not ordinary or vbmeta is not None, 'hash-only partition requires matching --vbmeta')
    original_vbmeta = parse(avb, vbmeta) if vbmeta else None
    if ordinary:
        require(summary(avb, original_vbmeta)['algorithm'] in
                ('SHA256_RSA2048', 'SHA256_RSA4096', 'SHA512_RSA2048', 'SHA512_RSA4096'),
                'unsupported vbmeta signing algorithm')
        bits = summary(avb, original_vbmeta)['algorithm'].rsplit('RSA', 1)[1]
        require(avb.RSAPublicKey(str(SUPPLIED / f'tools/pem/testkey_rsa{bits}.pem')).encode()
                == original_vbmeta[4], 'supplied signing key does not match vbmeta')
        require(original_vbmeta[1].rollback_index_location == 0,
                'unsupported vbmeta rollback index location')
        require(avb.verify_vbmeta_signature(original_vbmeta[1], original_vbmeta[3]),
                'input vbmeta signature is invalid')
        for name in ordinary:
            descriptors = [d for d in original_vbmeta[2]
                           if isinstance(d, avb.AvbHashDescriptor) and d.partition_name == name]
            require(len(descriptors) == 1, f'vbmeta: expected exactly one {name} descriptor')
            own = next(d for d in before[name][2] if isinstance(d, avb.AvbHashDescriptor))
            require(descriptors[0].encode() == own.encode(),
                    f'{name}: input footer and vbmeta describe different baselines')
    input_hashes = {str(p): sha256(p) for p in [*inputs.values(), *([vbmeta] if vbmeta else [])]}
    output.parent.mkdir(parents=True, exist_ok=True)
    records = []
    log = io.StringIO()
    old_cwd = Path.cwd()
    with tempfile.TemporaryDirectory(prefix='.sign-images-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'result'
        stage.mkdir()
        for name, path in inputs.items():
            shutil.copyfile(path, stage / f'{name}.img')
        if ordinary:
            shutil.copyfile(vbmeta, stage / 'vbmeta.img')

        def run_command(cmd, description='', capture_output=True):
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            records.append({'command': cmd, 'cwd': str(stage), 'input': None,
                            'stdout': result.stdout, 'stderr': result.stderr,
                            'exit_status': result.returncode})
            print(f'{description}\n{result.stdout}{result.stderr}', file=log)
            return result if result.returncode == 0 else None

        rebuild.PYTHON_EXECUTABLE = sys.executable
        rebuild.AvbImageParser.run_command = staticmethod(run_command)
        try:
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                # The actual supplied rebuild methods perform all signing operations.
                rebuilder = rebuild.AvbRebuilder(str(stage), str(ROOT / 'scripts/avbtool_host.py'),
                                                str(SUPPLIED / 'tools/pem/testkey_rsa4096.pem'))
                # Preserve the original per-algorithm key selection.
                rebuilder.private_key = None
                rebuilder.available_keys = [str(SUPPLIED / 'tools/pem/testkey_rsa2048.pem'),
                                           str(SUPPLIED / 'tools/pem/testkey_rsa4096.pem')]
                success = rebuilder.rebuild_all(list(inputs), True, not ordinary)
            require(success and all(r['exit_status'] == 0 for r in records), 'supplied signing tool failed')
            after = {name: parse(avb, stage / f'{name}.img') for name in inputs}
            for name, parsed in after.items():
                before_summary, after_summary = summary(avb, before[name]), summary(avb, parsed)
                for field in ('algorithm', 'rollback_index', 'rollback_index_location', 'flags',
                              'public_key_sha1'):
                    require(before_summary[field] == after_summary[field], f'{name}: changed {field}')
                require((stage / f'{name}.img').stat().st_size == inputs[name].stat().st_size,
                        f'{name}: partition size changed')
                require(avb.verify_vbmeta_signature(parsed[1], parsed[3]), f'{name}: invalid signature')
                original_data_size = before[name][0].original_image_size
                with inputs[name].open('rb') as source, (stage / f'{name}.img').open('rb') as signed:
                    require(source.read(original_data_size) == signed.read(original_data_size),
                            f'{name}: signing changed the patched payload')
                with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                    avb.Avb().verify_image(str(stage / f'{name}.img'), None, None, False, False)
                before_props = Counter(d.encode() for d in before[name][2]
                                       if isinstance(d, avb.AvbPropertyDescriptor))
                after_props = Counter(d.encode() for d in parsed[2]
                                      if isinstance(d, avb.AvbPropertyDescriptor))
                require(before_props == after_props, f'{name}: properties changed')
                require(before_summary['hashes'][name]['salt'] == after_summary['hashes'][name]['salt'],
                        f'{name}: salt changed')
            if ordinary:
                new_vbmeta = parse(avb, stage / 'vbmeta.img')
                require(avb.verify_vbmeta_signature(new_vbmeta[1], new_vbmeta[3]), 'invalid vbmeta signature')
                require(new_vbmeta[4] == original_vbmeta[4], 'vbmeta public key changed')
                for field in ('algorithm', 'rollback_index', 'rollback_index_location', 'flags'):
                    require(summary(avb, new_vbmeta)[field] == summary(avb, original_vbmeta)[field],
                            f'vbmeta: changed {field}')
                def unchanged(descriptors):
                    # The supplied rebuilder appends identical partition properties
                    # already present in vbmeta. Permit those duplicates only.
                    properties = {d.encode() for d in descriptors
                                  if isinstance(d, avb.AvbPropertyDescriptor)}
                    others = Counter(d.encode() for d in descriptors if not
                                     (isinstance(d, avb.AvbPropertyDescriptor) or
                                      (isinstance(d, avb.AvbHashDescriptor)
                                       and d.partition_name in ordinary)))
                    return properties, others
                require(unchanged(original_vbmeta[2]) == unchanged(new_vbmeta[2]),
                        'vbmeta: unrelated descriptors changed')
                for name in ordinary:
                    updated = [d for d in new_vbmeta[2] if isinstance(d, avb.AvbHashDescriptor)
                               and d.partition_name == name]
                    own = next(d for d in after[name][2] if isinstance(d, avb.AvbHashDescriptor))
                    require(len(updated) == 1 and updated[0].encode() == own.encode(),
                            f'vbmeta: {name} descriptor was not updated')
                after['vbmeta'] = new_vbmeta
            require(all(sha256(Path(path)) == digest for path, digest in input_hashes.items()),
                    'input changed during signing')
            outputs = {name + '.img': {'sha256': sha256(stage / f'{name}.img'),
                                       'bytes': (stage / f'{name}.img').stat().st_size,
                                       'avb': summary(avb, parsed)} for name, parsed in after.items()}
            backups = list(stage.glob('backup_*'))
            require(len(backups) == 1 and backups[0].is_dir(), 'expected one tool-created backup')
            rollback = '''#!/bin/sh
# Restore one explicit image copy to the tool's pre-signing baseline.
set -eu
[ "$#" -eq 1 ] || { printf 'usage: ROLLBACK.sh /absolute/path/to/image-copy.img\\n' >&2; exit 2; }
case "$1" in /*) ;; *) printf 'absolute copy path required\\n' >&2; exit 2 ;; esac
case "${1##*/}" in boot.img|init_boot.img|vbmeta.img) ;; *) printf 'unknown image name\\n' >&2; exit 2 ;; esac
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
baseline="$here/BACKUP_DIRECTORY/${1##*/}"
[ -f "$baseline" ] && [ ! -L "$baseline" ] && [ -f "$1" ] && [ ! -L "$1" ] || exit 2
cp "$baseline" "$1"
cmp -s "$baseline" "$1"
printf 'Restored pre-signing bytes: %s\\n' "$1"
'''.replace('BACKUP_DIRECTORY', backups[0].name)
            (stage / 'ROLLBACK.sh').write_text(rollback)
            (stage / 'ROLLBACK.sh').chmod(0o700)
            manifest = {'result': 'host-signature-verified', 'device_boot_verified': False,
                        'tool': str(SUPPLIED / 'rebuild_avb.py'), 'tool_hashes': PINS,
                        'inputs': input_hashes, 'outputs': outputs, 'commands': records,
                        'rollback': 'ROLLBACK.sh accepts an explicit image copy; restores pre-signing bytes from the sibling backup, not a pre-root device image',
                        'verification': 'partition signatures and hashes; vbmeta signature, updated hashes and unchanged unrelated descriptors (identical property duplicates allowed); other partitions not read'}
            (stage / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
            (stage / 'signing.log').write_text(log.getvalue())
            stage.rename(output)
        finally:
            os.chdir(old_cwd)
    return output / 'manifest.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--boot', type=Path)
    parser.add_argument('--init-boot', type=Path)
    parser.add_argument('--vbmeta', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    inputs = {name: path.absolute() for name, path in
              (('boot', args.boot), ('init_boot', args.init_boot)) if path}
    if not inputs:
        parser.error('provide --boot or --init-boot')
    try:
        manifest = sign(inputs, args.vbmeta.absolute() if args.vbmeta else None, args.output.absolute())
    except Exception as exc:
        print(f'sign-images: {exc}', file=sys.stderr)
        return 1
    print(f'Verified signed output: {manifest}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
