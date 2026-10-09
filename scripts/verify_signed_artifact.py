#!/usr/bin/env python3
"""Reopen downloaded Actions outputs, verify hashes/AVB, and exercise rollback."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile

from avbtool_host import ROOT, load_avbtool
from sign_images import parse, require, summary


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inspect_ramdisk(directory):
    binaries = directory.parent
    image = (directory / 'init_boot.img').read_bytes()
    require(image[:8] == b'ANDROID!' and struct.unpack_from('<I', image, 40)[0] == 4,
            'candidate requires boot header v4')
    kernel, ramdisk_size = struct.unpack_from('<II', image, 8)
    require(kernel == 0, 'init_boot unexpectedly contains a kernel')
    compressed = image[4096:4096 + ramdisk_size]
    if compressed[:2] == b'\x1f\x8b':
        data = gzip.decompress(compressed)
    elif compressed[:4] in (b'\x02\x21\x4c\x18', b'\x04\x22\x4d\x18'):
        lz4 = shutil.which('lz4')
        require(lz4 is not None, 'lz4 is required to inspect the candidate ramdisk')
        result = subprocess.run([lz4, '-d', '-c'], input=compressed, capture_output=True, timeout=30)
        require(result.returncode == 0, 'ramdisk decompression failed')
        data = result.stdout
    else:
        raise RuntimeError('unsupported ramdisk compression')
    entries = {}
    position = 0
    while position + 110 <= len(data):
        header = data[position:position + 110]
        require(header[:6] == b'070701', 'invalid newc CPIO entry')
        fields = [int(header[6 + i * 8:14 + i * 8], 16) for i in range(13)]
        mode, size, name_size = fields[1], fields[6], fields[11]
        position += 110
        name = data[position:position + name_size].rstrip(b'\0').decode()
        position = (position + name_size + 3) & ~3
        payload = data[position:position + size]
        require(len(payload) == size, 'truncated CPIO entry')
        position = (position + size + 3) & ~3
        if name == 'TRAILER!!!':
            break
        require(name not in entries, f'duplicate CPIO entry: {name}')
        entries[name] = (mode, payload)
    for name, expected in (('init', binaries / 'userspace/ksuinit'),
                           ('kernelsu.ko', binaries / 'lkm/android15-6.6_kernelsu.ko')):
        require(name in entries, f'candidate ramdisk missing {name}')
        require(hashlib.sha256(entries[name][1]).hexdigest() == sha(expected),
                f'candidate ramdisk {name} differs from the downloaded binary')
        require(entries[name][0] & 0o777 == 0o755, f'{name}: incorrect mode')
    config = entries.get('ksu_config', (0, b''))[1]
    require(b'allow_shell=1' not in config and b'norc=1' not in config,
            'candidate enabled an unapproved runtime option')
    commands = (binaries / 'lkm/compile_commands.json').read_text()
    require('-DCONFIG_KSU_DISABLE_MANAGER=1' in commands and
            '-DCONFIG_KSU_DEBUG=1' not in commands, 'no-Manager compiler configuration mismatch')
    bootstrap = binaries / 'userspace/bootstrap'
    embedded = None
    if bootstrap.exists():
        binary = bootstrap.read_bytes()
        module_bytes = entries['kernelsu.ko'][1]
        payload = (ROOT / '1_no_login.sh').read_bytes()
        require(hashlib.sha256(payload).hexdigest() ==
                'e9cb5f84428910e83c0c43957fa8deb01ce4fc3d7b899aaecea7810993c62200',
                'local approved payload changed')
        require(payload in binary, 'bootstrap does not embed the exact approved full script')
        runner = (ROOT / 'bin/root-control').read_bytes()
        require(runner in binary, 'bootstrap runner differs from approved entry')
        boot_script = (ROOT / 'scripts/autostart.sh').read_bytes()
        require(boot_script in binary, 'bootstrap autostart script differs from working source')
        require(b'root_control_prepare' in module_bytes and b'root_control_autostart' in module_bytes,
                'candidate is missing fixed init service definitions')
        embedded = {'bootstrap_sha256': hashlib.sha256(binary).hexdigest(),
                    'payload_sha256': hashlib.sha256(payload).hexdigest(),
                    'payload_bytes': len(payload), 'runner_sha256': hashlib.sha256(runner).hexdigest(),
                    'autostart_sha256': hashlib.sha256(boot_script).hexdigest(),
                    'exact_bootstrap_artifact': True, 'external_stage_required': False}
    ramdisk_bootstrap = entries.get('root-control-bootstrap')
    require(ramdisk_bootstrap is not None, 'init_boot ramdisk missing root-control-bootstrap')
    require(bootstrap.exists() and ramdisk_bootstrap[1] == bootstrap.read_bytes(),
            'init_boot ramdisk bootstrap differs from the signed artifact')
    require(ramdisk_bootstrap[0] & 0o777 == 0o500, 'bootstrap mode is not root-only executable')
    require(b'/dev/root-control-boot' not in entries['init'][1] and
            b'/dev/root-control-boot' not in entries['kernelsu.ko'][1],
            'candidate still depends on the old temporary /dev bootstrap path')
    return {'init_sha256': hashlib.sha256(entries['init'][1]).hexdigest(),
            'module_sha256': hashlib.sha256(entries['kernelsu.ko'][1]).hexdigest(),
            'ksu_config': config.decode(), 'entry_count': len(entries),
            'embedded_autostart': embedded,
            'ramdisk_bootstrap_sha256': hashlib.sha256(ramdisk_bootstrap[1]).hexdigest(),
            'ramdisk_bootstrap_mode': oct(ramdisk_bootstrap[0] & 0o777),
            'no_manager_define_verified': True, 'debug_define_present': False}


def verify(directory, report):
    manifest = json.loads((directory / 'manifest.json').read_text())
    require(manifest['result'] == 'host-signature-verified', 'unexpected artifact state')
    for name, metadata in manifest['outputs'].items():
        path = directory / name
        require(path.stat().st_size == metadata['bytes'] and sha(path) == metadata['sha256'],
                f'downloaded output mismatch: {name}')
    lock = json.loads((ROOT / 'config/upstreams.lock.json').read_text())
    for name, digest in lock['stock_inputs'].items():
        require(sha(directory / ('stock-' + name)) == digest, f'stock backup mismatch: {name}')
    avb = load_avbtool()
    signed = parse(avb, directory / 'init_boot.img')
    vbmeta = parse(avb, directory / 'vbmeta.img')
    stock_vbmeta = parse(avb, directory / 'stock-vbmeta.img')
    require(avb.verify_vbmeta_signature(vbmeta[1], vbmeta[3]), 'downloaded vbmeta signature invalid')
    require(vbmeta[4] == stock_vbmeta[4], 'downloaded vbmeta key differs from stock')
    changed_hashes = [d for d in vbmeta[2] if isinstance(d, avb.AvbHashDescriptor)
                      and d.partition_name == 'init_boot']
    own = next(d for d in signed[2] if isinstance(d, avb.AvbHashDescriptor))
    require(len(changed_hashes) == 1 and changed_hashes[0].encode() == own.encode(),
            'downloaded vbmeta does not describe downloaded init_boot')
    def unchanged(descriptors):
        return ({d.encode() for d in descriptors if isinstance(d, avb.AvbPropertyDescriptor)},
                Counter(d.encode() for d in descriptors if not
                        (isinstance(d, avb.AvbPropertyDescriptor) or
                         (isinstance(d, avb.AvbHashDescriptor) and d.partition_name == 'init_boot'))))
    require(unchanged(vbmeta[2]) == unchanged(stock_vbmeta[2]), 'unrelated AVB descriptors changed')
    records = []
    backups = list(directory.glob('backup_*'))
    require(len(backups) == 1, 'missing pre-signing backup')
    baseline = backups[0] / 'init_boot.img'
    baseline_hash = sha(baseline)
    require(baseline_hash in manifest['inputs'].values(), 'pre-signing backup hash differs from manifest')
    with tempfile.TemporaryDirectory(prefix='actions-image-rollback-') as temporary:
        copy = Path(temporary) / 'init_boot.img'
        command = [sys.executable, str(ROOT / 'scripts/avbtool_host.py'),
                   'verify_image', '--image', str(copy)]
        for label, source, expected in [('BASELINE', baseline, 1),
                                         ('MODIFIED', directory / 'init_boot.img', 0)]:
            shutil.copyfile(source, copy)
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            records.append({'name': label, 'command': command, 'input': None,
                            'stdout': result.stdout, 'stderr': result.stderr,
                            'exit_status': result.returncode, 'sha256': sha(copy)})
            require(result.returncode == expected, f'{label}: unexpected verification status')
        rollback = subprocess.run(['/bin/sh', str(directory / 'ROLLBACK.sh'), str(copy)],
                                  capture_output=True, text=True, timeout=30)
        records.append({'name': 'rollback-command', 'command': rollback.args,
                        'stdout': rollback.stdout, 'stderr': rollback.stderr,
                        'exit_status': rollback.returncode})
        require(rollback.returncode == 0 and sha(copy) == baseline_hash, 'rollback bytes mismatch')
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        records.append({'name': 'ROLLBACK', 'command': command, 'input': None,
                        'stdout': result.stdout, 'stderr': result.stderr,
                        'exit_status': result.returncode, 'sha256': sha(copy)})
        require(result.returncode == 1, 'rollback failed to restore the pre-signing verification state')
    ramdisk = inspect_ramdisk(directory)
    result = {'result': 'downloaded-artifact-verified', 'directory': str(directory.absolute()),
              'device_boot_verified': False, 'outputs': manifest['outputs'], 'records': records,
              'ramdisk': ramdisk,
              'avb': {'init_boot': summary(avb, signed), 'vbmeta': summary(avb, vbmeta)}}
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(f'Downloaded hashes, signatures, descriptor pairing and rollback verified: {report.absolute()}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--report', type=Path, default=ROOT / 'verification/actions-artifact-verification.json')
    args = parser.parse_args()
    verify(args.directory.resolve(), args.report)
