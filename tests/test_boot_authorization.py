#!/usr/bin/env python3
"""Compile the actual driver-FD gate and no-Manager grant function with UID fixtures."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
records = []


def function(text, signature):
    start = text.index(signature)
    brace = text.index('{', start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


baseline = (ROOT / 'upstream/KernelSU/kernel/supercall/supercall.c').read_text()
# Reconstruct patched sources on a throwaway copy; original checkout stays pristine.
with tempfile.TemporaryDirectory(prefix='root-gate-fixture-') as temporary:
    work = Path(temporary)
    for relative in ('kernel/supercall/supercall.c', 'kernel/policy/allowlist.c',
                     'kernel/runtime/ksud_integration.c', 'kernel/feature/sucompat.c',
                     'kernel/selinux/rules.c'):
        path = work / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / 'upstream/KernelSU' / relative, path)
    result = subprocess.run(['git', 'apply', str(ROOT / 'ci/autostart-kernel.patch')],
                            cwd=work, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    modified = (work / 'kernel/supercall/supercall.c').read_text()
    grants = function((work / 'kernel/policy/allowlist.c').read_text(),
                      'bool __ksu_is_allow_uid(uid_t uid)')
    for label, text in (('BASELINE', baseline), ('MODIFIED', modified)):
        source = '#include <errno.h>\n#include <stdbool.h>\n#include <stdio.h>\n' \
                 '#define CONFIG_KSU_DISABLE_MANAGER 1\n' \
                 'typedef unsigned int uid_t;\nstruct fixture_uid { uid_t val; };\n' \
                 'static uid_t uid;\nstatic struct fixture_uid current_uid(void) { return (struct fixture_uid){uid}; }\n' \
                 'static int ksu_install_fd_with_permissions(unsigned int flags, unsigned long permissions) { (void)flags; (void)permissions; return 42; }\n' \
                 '#define O_CLOEXEC 0\n' + function(text, 'int ksu_install_fd(void)') + '\n'
        if label == 'MODIFIED':
            source += grants + '\n'
        source += 'int main(void) { unsigned int uids[] = {0,2000,10000,10123,110123}; for (int i=0;i<5;i++) { uid=uids[i]; printf("uid=%u fd=%d",uid,ksu_install_fd());'
        if label == 'MODIFIED':
            source += 'printf(" app_grant=%d",__ksu_is_allow_uid(uid));'
        source += 'puts(""); } return 0; }\n'
        path = work / (label + '.c')
        path.write_text(source)
        binary = work / label
        compile_command = [shutil.which('clang') or shutil.which('cc'), '-std=c11', '-Wall', '-Wextra',
                           '-Wno-unused-parameter', str(path), '-o', str(binary)]
        result = subprocess.run(compile_command, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        result = subprocess.run([str(binary)], capture_output=True, text=True)
        records.append({'name': label, 'command': result.args, 'input_uids': [0, 2000, 10000, 10123, 110123],
                        'stdout': result.stdout, 'stderr': result.stderr, 'exit_status': result.returncode})
        assert result.returncode == 0
        if label == 'BASELINE':
            assert result.stdout.count('fd=42') == 5
        else:
            assert 'uid=0 fd=42 app_grant=0' in result.stdout
            assert result.stdout.count('fd=-1 app_grant=0') == 4
        print(label + '\n' + result.stdout, end='')
(ROOT / 'verification/autostart-authorization-tests.json').write_text(json.dumps(
    {'result': 'passed', 'native_functions': 'actual patched function bodies; UID and fd allocation simulated',
     'records': records}, indent=2) + '\n')
