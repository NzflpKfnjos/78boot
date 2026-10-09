#!/usr/bin/env python3
"""Real supplied-tool integration tests using modified firmware copies only."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / 'y700/TB322_ZUXOS_1.1.11.263_Tool/image'
OUT = ROOT / 'verification/signing-smoke'
records = []


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(name, args, expected):
    result = subprocess.run(args, capture_output=True, text=True, timeout=60)
    records.append({'name': name, 'command': args, 'input': None, 'stdout': result.stdout,
                    'stderr': result.stderr, 'exit_status': result.returncode})
    assert result.returncode == expected, (name, result.stdout, result.stderr)
    print('PASS ' + name)
    return result


def verify(name, path, expected):
    return run(name, [sys.executable, str(ROOT / 'scripts/avbtool_host.py'),
                      'verify_image', '--image', str(path)], expected)


def main():
    assert not OUT.exists(), 'fresh verification/signing-smoke directory required'
    OUT.mkdir()
    source_hashes = {name: sha(IMAGES / name) for name in ('boot.img', 'init_boot.img', 'vbmeta.img')}
    with tempfile.TemporaryDirectory(prefix='avb-copy-test-') as temporary:
        work = Path(temporary)
        for partition in ('boot', 'init_boot'):
            stock = IMAGES / f'{partition}.img'
            verify(partition + '-stock-valid', stock, 0)
            changed = work / f'{partition}.img'
            shutil.copyfile(stock, changed)
            with changed.open('r+b') as stream:
                stream.seek(64)  # Signed command-line byte, not executable code.
                previous = stream.read(1)
                stream.seek(64)
                stream.write(bytes([previous[0] ^ 1]))
            changed_sha = sha(changed)
            verify(partition + '-BASELINE-unsigned-copy-rejected', changed, 1)
            signed = OUT / partition
            command = [sys.executable, str(ROOT / 'scripts/sign_images.py'),
                       '--' + partition.replace('_', '-'), str(changed), '--output', str(signed)]
            if partition == 'init_boot':
                missing = OUT / 'missing-vbmeta'
                run('missing-vbmeta-rejected', [*command[:-1], str(missing)], 1)
                assert not missing.exists()
                command.extend(['--vbmeta', str(IMAGES / 'vbmeta.img')])
            run(partition + '-signing', command, 0)
            verify(partition + '-MODIFIED-signed-copy-valid', signed / f'{partition}.img', 0)
            manifest = json.loads((signed / 'manifest.json').read_text())
            assert manifest['device_boot_verified'] is False
            assert sha(changed) == changed_sha
            assert set(manifest['outputs']) == ({'boot.img'} if partition == 'boot'
                                                else {'init_boot.img', 'vbmeta.img'})
            run(partition + '-overwrite-rejected', command, 1)
            rollback = work / 'rollback'
            rollback.mkdir(exist_ok=True)
            restored = rollback / f'{partition}.img'
            shutil.copyfile(signed / f'{partition}.img', restored)
            run(partition + '-rollback-command', [str(signed / 'ROLLBACK.sh'), str(restored)], 0)
            assert sha(restored) == changed_sha
            verify(partition + '-ROLLBACK-pre-signing-copy-rejected', restored, 1)
            assert sha(signed / f'{partition}.img') == manifest['outputs'][f'{partition}.img']['sha256']
        raw = work / 'raw-init_boot.img'
        shutil.copyfile(IMAGES / 'init_boot.img', raw)
        # Remove the footer as an upstream image patcher may do.
        with raw.open('r+b') as stream:
            stream.truncate(2465792)
            stream.seek(64)
            stream.write(b'X')
        prepared = work / 'prepared-init_boot.img'
        run('patched-footer-preparation', [sys.executable, str(ROOT / 'scripts/prepare_avb_input.py'),
            '--stock', str(IMAGES / 'init_boot.img'), '--patched', str(raw), '--output', str(prepared)], 0)
        run('prepared-image-signing', [sys.executable, str(ROOT / 'scripts/sign_images.py'),
            '--init-boot', str(prepared), '--vbmeta', str(IMAGES / 'vbmeta.img'),
            '--output', str(OUT / 'prepared')], 0)
        verify('prepared-image-signature-valid', OUT / 'prepared/init_boot.img', 0)
        run('wrong-partition-rejected', [sys.executable, str(ROOT / 'scripts/sign_images.py'),
            '--boot', str(IMAGES / 'init_boot.img'), '--output', str(OUT / 'wrong-partition')], 1)
        assert not (OUT / 'wrong-partition').exists()
    assert all(sha(IMAGES / name) == digest for name, digest in source_hashes.items())
    report = {'result': 'passed', 'device_boot_verified': False, 'original_hashes_unchanged': source_hashes,
              'test_changes': 'one command-line byte in copies; these are test images, not root builds',
              'records': records}
    (ROOT / 'verification/signing-tests.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(f'Evidence: {ROOT / "verification/signing-tests.json"}')


if __name__ == '__main__':
    main()
