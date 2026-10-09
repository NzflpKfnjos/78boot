#!/usr/bin/env python3
"""Host policy tests. Android UID/ownership are explicitly simulated.
Never execute the real payload. Use a harmless pinned fixture instead.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'bin/root-control'
records = []

with tempfile.TemporaryDirectory(prefix='root-control-test-') as tmp:
    work = Path(tmp)
    adb = work / 'adb'
    base = adb / 'root-control'
    script = base / 'scripts/1_no_login.sh'
    runner = base / 'bin/root-control'
    adapter = work / 'toybox'
    adapter.write_text('#!' + sys.executable + '\n' + '''import hashlib, os, pathlib, subprocess, sys
args = sys.argv[1:]
if args == ['id', '-u']:
    print(os.environ.get('TEST_CALLER_UID', '0'))
elif args[0] == 'stat':
    p = pathlib.Path(args[-1])
    print('0:0:' + format(p.stat().st_mode & 0o777, 'o'))
elif args[0] == 'sha256sum':
    print(hashlib.sha256(pathlib.Path(args[1]).read_bytes()).hexdigest() + '  ' + args[1])
elif args[0] == 'chown':
    pass  # Host ownership is simulated, never elevate the test process.
else:
    command = '/usr/bin/' + args[0]
    if not pathlib.Path(command).exists(): command = '/bin/' + args[0]
    sys.exit(subprocess.call([command] + args[1:]))
''')
    adapter.chmod(0o700)
    fixture = b'#!/bin/sh\nprintf "fixture mode=%s secret=%s\\n" "$1" "${INJECTED_SECRET-unset}"\nexit 7\n'
    fixture_hash = hashlib.sha256(fixture).hexdigest()
    original = SOURCE.read_text()
    transformed = (original.replace('/system/bin/toybox', str(adapter))
                   .replace('/system/bin/sh', '/bin/sh')
                   .replace('PATH=/system/bin', 'PATH=/usr/bin:/bin')
                   .replace('/data/adb', str(adb))
                   .replace('e9cb5f84428910e83c0c43957fa8deb01ce4fc3d7b899aaecea7810993c62200', fixture_hash))

    def reset():
        if adb.exists(): shutil.rmtree(adb)
        (base / 'bin').mkdir(parents=True)
        (base / 'scripts').mkdir()
        for p in (adb, base, base / 'bin', base / 'scripts'): p.chmod(0o700)
        runner.write_text(transformed)
        runner.chmod(0o500)
        script.write_bytes(fixture)
        script.chmod(0o400)

    def run(name, path, args, status, needle, uid='0'):
        command = ['/bin/sh', str(path), *args]
        result = subprocess.run(command, text=True, capture_output=True,
                                env={'TEST_CALLER_UID': uid, 'INJECTED_SECRET': 'must-not-pass'})
        records.append({'name': name, 'command': command, 'input': args,
                        'simulated_uid': uid, 'stdout': result.stdout,
                        'stderr': result.stderr, 'exit_status': result.returncode})
        assert result.returncode == status, (name, result)
        assert needle in result.stdout + result.stderr, (name, result)
        print('PASS ' + name)

    reset()
    for mode in ('1', '2'):
        run('approved-mode-' + mode, runner, [mode], 7, 'fixture mode=' + mode + ' secret=unset')
    run('non-root-denied', runner, ['1'], 1, 'root caller required', uid='2000')
    run('invalid-mode-denied', runner, ['sh'], 1, 'only mode')
    run('extra-argument-denied', runner, ['1', '/bin/sh'], 1, 'usage:')
    run('missing-argument-denied', runner, [], 1, 'usage:')
    script.chmod(0o600)
    script.write_bytes(fixture + b'#changed\n')
    script.chmod(0o400)
    run('tampered-script-denied', runner, ['1'], 1, 'hash mismatch')
    reset()
    script.chmod(0o666)
    run('writable-script-denied', runner, ['1'], 1, 'file ownership or permissions')
    reset()
    (base / 'scripts').chmod(0o777)
    run('writable-directory-denied', runner, ['1'], 1, 'directory must')
    reset()
    script.unlink()
    script.symlink_to(work / 'outside')
    run('symlink-script-denied', runner, ['1'], 1, 'invalid installation file')
    reset()
    script.unlink()
    run('missing-script-denied', runner, ['1'], 1, 'invalid installation file')

    # Exercise a new-file transaction: absent baseline -> added copy -> absent.
    transaction = work / 'transaction-runner'
    run('BASELINE', transaction, ['1'], 127 if sys.platform == 'darwin' else 2, 'No such file')
    transaction.write_text(transformed)
    transaction.chmod(0o500)
    reset()
    run('MODIFIED', transaction, ['1'], 7, 'fixture mode=1 secret=unset')
    result = subprocess.run(['/bin/sh', str(ROOT / 'scripts/ROLLBACK.sh'), str(transaction)],
                            text=True, capture_output=True)
    records.append({'name': 'rollback-command', 'command': result.args,
                    'stdout': result.stdout, 'stderr': result.stderr,
                    'exit_status': result.returncode})
    assert result.returncode == 0 and not transaction.exists()
    run('ROLLBACK', transaction, ['1'], 127 if sys.platform == 'darwin' else 2, 'No such file')

    # Exercise installation with the harmless fixture and host-adapted runner.
    reset()
    src_script = work / 'fixture.sh'
    src_script.write_bytes(fixture)
    src_runner = work / 'source-runner'
    src_runner.write_text(transformed)
    installer = work / 'install.sh'
    installer_text = (ROOT / 'scripts/install.sh').read_text()
    installer_text = (installer_text.replace('/system/bin/toybox', str(adapter))
                      .replace('/data/adb', str(adb))
                      .replace('e9cb5f84428910e83c0c43957fa8deb01ce4fc3d7b899aaecea7810993c62200', fixture_hash)
                      .replace(hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
                               hashlib.sha256(src_runner.read_bytes()).hexdigest()))
    installer.write_text(installer_text)
    shutil.rmtree(base)
    run('install-non-root-denied', installer, [str(src_script), str(src_runner)], 1,
        'root caller required', uid='2000')
    src_script.write_bytes(b'tampered\n')
    run('install-tampered-source-denied', installer, [str(src_script), str(src_runner)], 1,
        'source script hash mismatch')
    assert not base.exists()
    src_script.write_bytes(fixture)
    run('install-approved', installer, [str(src_script), str(src_runner)], 0, 'Installed:')
    run('installed-runner', runner, ['1'], 7, 'fixture mode=1 secret=unset')
    run('install-overwrite-denied', installer, [str(src_script), str(src_runner)], 1,
        'installation already exists')

output = ROOT / 'verification/VERIFICATION.txt'
output.parent.mkdir(exist_ok=True)
output.write_text('Host policy verification; NOT Android device validation.\n'
                  'Initial test command: python3 tests/test_root_control.py; exit=1.\n'
                  'Initial failure: host adapter attempted /bin/sha256sum (absent on macOS); FileNotFoundError; runner stderr root-control: cannot hash script.\n'
                  'Correction: compute SHA-256 in the host adapter using Python hashlib.\n'
                  'Second test attempt: exit=1; macOS /bin/sh absent script returned 127 / No such file or directory, rather than assumed 2 / cannot open.\n'
                  'Correction: use platform-specific absent-script status, with literal results recorded below.\n'
                  'Caller UID and root ownership are simulated by a host adapter.\n'
                  'Payload is a harmless fixture; production script was never executed.\n'
                  'BASELINE and ROLLBACK are absent-file states for this newly added entry.\n'
                  'No original file hash exists for the absent baseline.\n'
                  'Changed entry: bin/root-control fixed path/hash/argument authorization.\n'
                  + json.dumps({'artifacts': {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest()
                                             for p in [SOURCE, ROOT / 'scripts/install.sh',
                                                       ROOT / 'scripts/ROLLBACK.sh',
                                                       ROOT / 'verification/root-control.patch']},
                                'records': records}, ensure_ascii=False, indent=2) + '\n')
print('Evidence: ' + str(output.resolve()))
