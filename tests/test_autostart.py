#!/usr/bin/env python3
"""Exercise boot timing, literal 1+Enter input, once-per-boot, disable and exit status."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
records = []

with tempfile.TemporaryDirectory(prefix='autostart-fixture-') as temporary:
    work = Path(temporary)
    base = work / 'adb/root-control'
    (base / 'bin').mkdir(parents=True)
    (base / 'state').mkdir()
    (base / 'logs').mkdir()
    (base / 'state/prepared').write_text('fixture-boot-1\n')
    boot_id = work / 'boot-id'
    boot_id.write_text('fixture-boot-1\n')
    disable = work / 'disable'
    tools = work / 'tools'
    tools.mkdir()
    getprop = tools / 'getprop'
    getprop.write_text('#!/bin/sh\nprintf "%s\\n" "${TEST_BOOT_COMPLETED:-1}"\n')
    getprop.chmod(0o700)
    log = tools / 'log'
    log.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >&2\n')
    log.chmod(0o700)
    toybox = tools / 'toybox'
    toybox.write_text('#!' + sys.executable + '\n' + '''import os, pathlib, subprocess, sys
args=sys.argv[1:]
if args == ['id','-u']:
    print(os.environ.get('TEST_UID','0'))
else:
    command='/usr/bin/'+args[0]
    if not pathlib.Path(command).exists(): command='/bin/'+args[0]
    sys.exit(subprocess.call([command,*args[1:]]))
''')
    toybox.chmod(0o700)
    runner = base / 'bin/root-control'
    runner.write_text('''#!/bin/sh
IFS= read -r choice
printf 'arg=%s stdin=%s\\n' "$1" "$choice"
printf 'run\\n' >> "$TEST_COUNT"
exit 7
''')
    runner.chmod(0o700)
    count = work / 'count'
    source = (ROOT / 'scripts/autostart.sh').read_text()
    adapted = source.replace('PATH=/system/bin', f'PATH={tools}:/usr/bin:/bin')
    adapted = adapted.replace('BASE=/data/adb/root-control', f'BASE={base}')
    adapted = adapted.replace('TB=/system/bin/toybox', f'TB={toybox}')
    adapted = adapted.replace('/data/local/tmp/root-control.disable', str(disable))
    adapted = adapted.replace('/proc/sys/kernel/random/boot_id', str(boot_id))
    changed = work / 'autostart.sh'
    changed.write_text(adapted)
    def run(name, path, expected, **extra):
        env = {**os.environ, 'TEST_COUNT': str(count), **extra}
        args = ['/bin/sh', str(path)]
        result = subprocess.run(args, capture_output=True, text=True, env=env, timeout=10)
        records.append({'name': name, 'command': args, 'environment': extra,
                        'stdout': result.stdout, 'stderr': result.stderr,
                        'exit_status': result.returncode})
        assert result.returncode == expected, (name, result)
        print('PASS ' + name)
    old = work / 'absent-service.sh'
    run('BASELINE-no-autostart', old, 127 if sys.platform == 'darwin' else 2)
    assert not count.exists()
    run('MODIFIED-mode1-and-newline', changed, 7)
    assert (base / 'logs/payload.log').read_text() == 'arg=1 stdin=1\n'
    assert (base / 'state/autostart.status').read_text() == 'exited=7\n'
    run('same-boot-does-not-repeat', changed, 0)
    assert count.read_text() == 'run\n'
    boot_id.write_text('fixture-boot-2\n')
    (base / 'state/prepared').write_text('fixture-boot-2\n')
    run('next-boot-runs-again', changed, 7)
    assert count.read_text() == 'run\nrun\n'
    disable.touch()
    boot_id.write_text('fixture-boot-3\n')
    run('owner-disable-marker', changed, 0)
    assert count.read_text() == 'run\nrun\n'
    disable.unlink()
    run('ordinary-caller-denied', changed, 1, TEST_UID='2000')
    run('before-boot-complete-denied', changed, 1, TEST_BOOT_COMPLETED='0')
    copy = work / 'rollback-copy.sh'
    shutil.copyfile(changed, copy)
    result = subprocess.run([str(ROOT / 'scripts/ROLLBACK.sh'), str(copy)], capture_output=True, text=True)
    assert result.returncode == 0 and not copy.exists()
    records.append({'name':'rollback-command','command':result.args,'stdout':result.stdout,
                    'stderr':result.stderr,'exit_status':result.returncode})
    run('ROLLBACK-no-autostart', copy, 127 if sys.platform == 'darwin' else 2)
    assert count.read_text() == 'run\nrun\n'
output=ROOT / 'verification/autostart-host-tests.json'
output.write_text(json.dumps({'result':'passed','payload':'harmless fixture; UID simulated',
                               'records':records},ensure_ascii=False,indent=2)+'\n')
print('Evidence:',output)
