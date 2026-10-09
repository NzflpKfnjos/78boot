#!/usr/bin/env python3
"""Attach stock AVB metadata to a patched image for the supplied rebuilder.

The resulting intermediate image has stale hashes and MUST go through
sign_images.py. No original bytes are changed. Supports boot header v4 only.
"""
import argparse
import hashlib
from pathlib import Path
import struct

from avbtool_host import load_avbtool
from sign_images import parse, require


def prepare(stock, patched, output):
    require(not output.exists(), 'output exists')
    avb = load_avbtool()
    footer, _, _, blob, _ = parse(avb, stock)
    require(footer is not None, 'stock AVB footer missing')
    data = patched.read_bytes()
    require(data[:8] == b'ANDROID!' and struct.unpack_from('<I', data, 40)[0] == 4,
            'only Android boot header v4 is supported')
    kernel, ramdisk = struct.unpack_from('<II', data, 8)
    signature = struct.unpack_from('<I', data, 1580)[0]
    align = lambda n: (n + 4095) // 4096 * 4096
    size = 4096 + align(kernel) + align(ramdisk) + align(signature)
    require(size <= len(data), 'patched image is truncated')
    partition_size = stock.stat().st_size
    require(size + align(len(blob)) + 4096 <= partition_size, 'patched payload exceeds stock partition')
    footer.original_image_size = size
    footer.vbmeta_offset = size
    footer.vbmeta_size = len(blob)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as stream:
        stream.write(data[:size])
        stream.write(blob)
        stream.seek(partition_size - footer.SIZE)
        stream.write(footer.encode())
    require(output.stat().st_size == partition_size, 'intermediate partition size mismatch')
    print(f'Unsigned intermediate (signing required): {output.absolute()}')
    print(f'patched_payload_bytes={size} sha256={hashlib.sha256(data[:size]).hexdigest()}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', type=Path, required=True)
    parser.add_argument('--patched', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    prepare(args.stock, args.patched, args.output)


if __name__ == '__main__':
    main()
