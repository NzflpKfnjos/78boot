#!/usr/bin/env python3
"""Inspect firmware and the pinned self-extracting script without running them."""
import argparse
import gzip
import hashlib
import io
import json
import mmap
from pathlib import Path
import re
import struct
import tarfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_SHA256 = 'e9cb5f84428910e83c0c43957fa8deb01ce4fc3d7b899aaecea7810993c62200'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def elf(data):
    if data[:6] != b'\x7fELF\x02\x01':
        raise ValueError('expected ELF64 little-endian')
    fields = struct.unpack_from('<HHIQQQIHHHHHH', data, 16)
    machine, phoff, phentsize, phnum = fields[1], fields[4], fields[8], fields[9]
    segments = [struct.unpack_from('<IIQQQQQQ', data, phoff + i * phentsize) for i in range(phnum)]
    result = {'class': 64, 'endianness': 'little', 'machine': machine,
              'architecture': {183: 'aarch64', 62: 'x86_64'}.get(machine, 'unknown'),
              'interpreter': None, 'needed_libraries': []}
    dynamic = []
    for kind, _, offset, _, _, size, _, _ in segments:
        if kind == 3:
            result['interpreter'] = data[offset:offset + size].rstrip(b'\0').decode()
        elif kind == 2:
            for index in range(offset, offset + size, 16):
                tag, value = struct.unpack_from('<qQ', data, index)
                if tag == 0:
                    break
                dynamic.append((tag, value))
    strtab = next((v for t, v in dynamic if t == 5), None)
    if strtab is not None:
        for kind, _, offset, address, _, size, _, _ in segments:
            if kind == 1 and address <= strtab < address + size:
                strings = offset + strtab - address
                for tag, value in dynamic:
                    if tag == 1:
                        start = strings + value
                        end = data.index(b'\0', start)
                        result['needed_libraries'].append(data[start:end].decode())
                break
        else:
            raise ValueError('ELF string table not mapped to a file segment')
    return result


def inspect_images(images, output):
    facts = {}
    for name in ('boot.img', 'init_boot.img', 'vendor_boot.img', 'vbmeta.img', 'vmlinux'):
        path = images / name
        item = {'path': str(path), 'bytes': path.stat().st_size, 'sha256': digest(path)}
        with path.open('rb') as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
            if data[:8] == b'ANDROID!':
                item['header_version'] = struct.unpack_from('<I', data, 40)[0]
                if item['header_version'] not in (3, 4):
                    raise ValueError('only Android boot header v3/v4 is supported')
                item['kernel_bytes'], item['ramdisk_bytes'] = struct.unpack_from('<II', data, 8)
                item['header_os_version_raw'] = struct.unpack_from('<I', data, 16)[0]
            elif data[:8] == b'VNDRBOOT':
                item['header_version'], item['page_size'] = struct.unpack_from('<II', data, 8)
            banners = re.findall(rb'Linux version [^\x00\n]{1,400}', data)
            item['linux_banners'] = list(dict.fromkeys(b.decode() for b in banners))
            start = data.find(b'IKCFG_ST')
            end = data.find(b'IKCFG_ED', start + 8) if start >= 0 else -1
            if end > start >= 0:
                config = gzip.decompress(data[start + 8:end])
                config_path = output / (name + '.config')
                config_path.write_bytes(config)
                item['config_path'] = str(config_path)
                item['config_sha256'] = hashlib.sha256(config).hexdigest()
                wanted = ('CONFIG_ARM64', 'CONFIG_ARM64_4K_PAGES', 'CONFIG_ARM64_PAGE_SHIFT',
                          'CONFIG_MODULES', 'CONFIG_MODVERSIONS', 'CONFIG_KPROBES', 'CONFIG_KALLSYMS',
                          'CONFIG_KALLSYMS_ALL', 'CONFIG_CFI_CLANG', 'CONFIG_MODULE_SIG_FORCE',
                          'CONFIG_MODULE_SIG_PROTECT', 'CONFIG_EXT4_FS', 'CONFIG_SECURITY_SELINUX')
                item['config_subset'] = [line for line in config.decode().splitlines()
                                         if any(line.startswith(key + '=') or line == f'# {key} is not set'
                                                for key in wanted)]
        facts[name] = item
    partitions = []
    for source in sorted(images.glob('rawprogram*.xml')):
        for item in ET.parse(source).getroot().findall('program'):
            if re.fullmatch(r'(boot|init_boot|vendor_boot|recovery|vbmeta)_[ab]', item.get('label', '')):
                partitions.append({'source': str(source), 'label': item.get('label'),
                                   'filename': item.get('filename'),
                                   'bytes': int(item.get('num_partition_sectors'))
                                            * int(item.get('SECTOR_SIZE_IN_BYTES'))})
    return {'images': facts, 'partitions': partitions,
            'vmlinux_banner_matches_boot': facts['boot.img']['linux_banners'] == facts['vmlinux']['linux_banners'],
            'running_device_confirmed': False}


def inspect_script(script):
    if digest(script) != SCRIPT_SHA256:
        raise ValueError('target script SHA-256 differs from the pinned baseline')
    data = script.read_bytes()
    # The script's literal tail offset controls the binary boundary; never rewrite it.
    match = re.search(rb'tail -n \+(\d+) "\$0"', data[:16384])
    if not match:
        raise ValueError('payload offset missing')
    count = int(match.group(1))
    header = data.split(b'\n', count - 1)
    if len(header) != count:
        raise ValueError('payload offset exceeds file')
    expected = dict(re.findall(rb'(expected_sha|expected_helper_sha)=([0-9a-f]{64})', data[:16384]))
    payloads = {}
    with tarfile.open(fileobj=io.BytesIO(header[-1]), mode='r:gz') as archive:
        for member in archive.getmembers():
            if member.name not in ('payload.elf', 'config_fetch_helper.elf') or not member.isfile():
                raise ValueError(f'unexpected embedded member: {member.name}')
            if member.name in payloads:
                raise ValueError('duplicate embedded member')
            content = archive.extractfile(member).read()
            sha = hashlib.sha256(content).hexdigest()
            key = b'expected_sha' if member.name == 'payload.elf' else b'expected_helper_sha'
            if sha != expected[key].decode():
                raise ValueError(f'embedded ELF hash mismatch: {member.name}')
            payloads[member.name] = {'bytes': len(content), 'sha256': sha, **elf(content)}
    if set(payloads) != {'payload.elf', 'config_fetch_helper.elf'}:
        raise ValueError('embedded member set mismatch')
    return {'path': str(script), 'sha256': SCRIPT_SHA256, 'payload_line': count,
            'executed': False, 'payloads': payloads}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--images', type=Path, default=ROOT / 'y700/TB322_ZUXOS_1.1.11.263_Tool/image')
    parser.add_argument('--script', type=Path, default=ROOT.parent / 'tomato/release/1_no_login.sh')
    parser.add_argument('--output', type=Path, default=ROOT / 'verification/stage0')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    result = {**inspect_images(args.images.resolve(), output),
              'script': inspect_script(args.script.resolve())}
    destination = output / 'baseline.json'
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(f'Baseline evidence: {destination}')
    print(result['images']['boot.img']['linux_banners'][0])
    for name, payload in result['script']['payloads'].items():
        print(f'{name}: {payload["architecture"]}; interpreter={payload["interpreter"]}; '
              f'needed={payload["needed_libraries"]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
