#!/usr/bin/env python3
"""Run the supplied avbtool unchanged with this host's Python and OpenSSL."""
import hashlib
import importlib.util
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
SUPPLIED = ROOT / '签名工具/avb'
PINS = {
    'rebuild_avb.py': '1acf9fb370b4575eb629d7d417c67a768869e5176e8cf63956ba0639d3c4f5f8',
    'tools/avbtool.py': '76a72c9509bd8c79fac1151772cf965ef615d9ba6a9d9579d10d873b7c7f2a65',
    'tools/pem/testkey_rsa2048.pem': 'f1d5765a2bdfb92fb08aee021107c7ac1a7a3f590dafd853771c85375ef0fbd7',
    'tools/pem/testkey_rsa4096.pem': '88bd0278559c5f54b709560987f2f5dd67f1afe42b2c6576a2d5fdf5fe0c6d69',
}


def load_supplied(relative, name):
    path = SUPPLIED / relative
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != PINS[relative]:
        raise RuntimeError(f'supplied tool hash mismatch: {path}')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_avbtool():
    module = load_supplied('tools/avbtool.py', 'supplied_avbtool')
    openssl = shutil.which('openssl')
    if not openssl:
        raise RuntimeError('openssl is required')
    module.OPENSSL_EXECUTABLE = openssl
    return module


if __name__ == '__main__':
    load_avbtool().AvbTool().run(sys.argv)
