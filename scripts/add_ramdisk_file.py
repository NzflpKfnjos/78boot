#!/usr/bin/env python3
"""Add one exact executable to an Android v4 init_boot ramdisk.

The input is the unfooted patched image produced by ksud. The output is
another unfooted image; prepare_avb_input.py attaches stock AVB metadata later.
"""
import argparse
import hashlib
from pathlib import Path
import subprocess
import struct
import tempfile


def align(value, amount=4):
    return (value + amount - 1) & ~(amount - 1)


def cpio_entries(data):
    entries = []
    position = 0
    while position + 110 <= len(data):
        header = data[position:position + 110]
        if header[:6] != b'070701':
            raise ValueError('unsupported or malformed newc CPIO')
        fields = [int(header[6 + i * 8:14 + i * 8], 16) for i in range(13)]
        mode, size, name_size = fields[1], fields[6], fields[11]
        position += 110
        name = data[position:position + name_size].rstrip(b'\0').decode()
        position = align(position + name_size)
        payload = data[position:position + size]
        if len(payload) != size:
            raise ValueError(f'truncated CPIO member: {name}')
        position = align(position + size)
        if name == 'TRAILER!!!':
            return entries, position
        entries.append((name, mode, payload))
    raise ValueError('CPIO trailer missing')


def cpio_entry(name, mode, payload):
    name_bytes = name.encode() + b'\0'
    fields = [0, mode, 0, 0, 1, 0, len(payload), 0, 0, 0, 0, len(name_bytes), 0]
    header = b'070701' + b''.join(f'{field:08x}'.encode() for field in fields)
    return header + name_bytes + b'\0' * (align(110 + len(name_bytes)) - 110 - len(name_bytes)) + payload + b'\0' * (align(len(payload)) - len(payload))


def add(stock, bootstrap, output):
    data = stock.read_bytes()
    if data[:8] != b'ANDROID!' or struct.unpack_from('<I', data, 40)[0] != 4:
        raise ValueError('Android boot header v4 required')
    kernel_size, ramdisk_size = struct.unpack_from('<II', data, 8)
    if kernel_size:
        raise ValueError('init_boot input unexpectedly contains a kernel')
    page = 4096
    ramdisk_start = page
    ramdisk_end = ramdisk_start + ramdisk_size
    compressed = data[ramdisk_start:ramdisk_end]
    with tempfile.TemporaryDirectory(prefix='ramdisk-add-') as temporary:
        temp = Path(temporary)
        compressed_path = temp / 'ramdisk.lz4'
        plain_path = temp / 'ramdisk.cpio'
        new_compressed_path = temp / 'ramdisk-new.lz4'
        compressed_path.write_bytes(compressed)
        result = subprocess.run(['lz4', '-d', '-f', str(compressed_path), str(plain_path)], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError(f'lz4 decode failed: {result.stderr}')
        entries, trailer_position = cpio_entries(plain_path.read_bytes())
        if any(name == 'root-control-bootstrap' for name, _, _ in entries):
            raise ValueError('root-control-bootstrap already exists')
        bootstrap_data = bootstrap.read_bytes()
        if bootstrap_data[:6] != b'\x7fELF\x02\x01' or int.from_bytes(bootstrap_data[18:20], 'little') != 183:
            raise ValueError('bootstrap must be an AArch64 ELF64 executable')
        rebuilt = b''.join(cpio_entry(name, mode, payload) for name, mode, payload in entries)
        rebuilt += cpio_entry('root-control-bootstrap', 0o100500, bootstrap_data)
        rebuilt += cpio_entry('TRAILER!!!', 0o0700, b'')
        plain_path.write_bytes(rebuilt)
        result = subprocess.run(['lz4', '-l', '-f', str(plain_path), str(new_compressed_path)], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError(f'lz4 encode failed: {result.stderr}')
        new_compressed = new_compressed_path.read_bytes()
    header = bytearray(data[:page])
    struct.pack_into('<I', header, 12, len(new_compressed))
    payload_start = page
    image = bytes(header) + new_compressed + data[ramdisk_end:]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(image)
    print(f'Added root-control-bootstrap: bytes={len(bootstrap_data)} sha256={hashlib.sha256(bootstrap_data).hexdigest()}')
    print(f'New ramdisk bytes={len(new_compressed)} output={output.absolute()}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--bootstrap', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    add(args.image, args.bootstrap, args.output)
