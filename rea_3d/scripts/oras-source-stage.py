"""Read-only ZIP source identification and bounded staging of its one 3DS image.
SPDX-License-Identifier: GPL-3.0-or-later
No game executable or archive instructions are executed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import time
import zipfile


def describe_header(stream):
    stream.seek(0)
    header = stream.read(0x200)
    if header[0x100:0x104] != b'NCSD':
        raise ValueError('Expected NCSD image')
    partitions = []
    for index in range(8):
        offset, length = struct.unpack_from('<II', header, 0x120+index*8)
        if not length:
            continue
        offset, length = offset*512, length*512
        stream.seek(offset)
        ncch = stream.read(0x200)
        if ncch[0x100:0x104] != b'NCCH':
            raise ValueError('Missing NCCH header at declared partition offset')
        unit = 512 << ncch[0x18E]
        romfs_offset, romfs_length = struct.unpack_from('<II', ncch, 0x1B0)
        record = {'index': index, 'offset': offset, 'bytes': length,
                  'product_code': ncch[0x150:0x160].decode('ascii', 'replace').rstrip('\0'),
                  'program_id': f'{struct.unpack_from("<Q",ncch,0x118)[0]:016x}',
                  'flags_hex': ncch[0x188:0x190].hex(),
                  'declares_no_crypto': bool(ncch[0x18F]&4),
                  'romfs_offset': offset+romfs_offset*unit, 'romfs_bytes': romfs_length*unit}
        if romfs_offset and romfs_length:
            stream.seek(record['romfs_offset'])
            prefix = stream.read(0x100)
            record['romfs_magic_hex'] = prefix[:16].hex()
            record['romfs_ivfc_header_present'] = prefix[:4] == b'IVFC'
        partitions.append(record)
    return {'magic': 'NCSD', 'declared_image_bytes': struct.unpack_from('<I',header,0x104)[0]*512,
            'partitions': partitions}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--zip', required=True)
    parser.add_argument('--output-dir', required=True)
    ns=parser.parse_args()
    source=Path(ns.zip).resolve(strict=True)
    destination=Path(ns.output_dir)
    if not destination.is_absolute() or destination.drive.upper() != 'D:':
        raise ValueError('Staging must use an absolute caller-selected D: directory')
    if destination.exists():
        raise ValueError('Output directory already exists')
    before=source.stat()
    with zipfile.ZipFile(source) as archive:
        entries=archive.infolist()
        candidates=[entry for entry in entries if not entry.is_dir() and entry.filename.lower().endswith('.3ds')]
        if len(candidates)!=1:
            raise ValueError('Exactly one 3DS member must be selected')
        member=candidates[0]
        if member.flag_bits&1 or member.file_size>4*1024**3:
            raise ValueError('Encrypted ZIP member or oversized source')
        if shutil.disk_usage(destination.parent).free < member.file_size + 2*1024**3:
            raise ValueError('Insufficient staging space with 2GiB reserve')
        destination.mkdir(exist_ok=False)
        target=destination/'source.3ds'
        digest=hashlib.sha256()
        copied=0
        started=time.monotonic()
        with archive.open(member) as input_stream, target.open('xb') as output:
            while chunk:=input_stream.read(8*1024*1024):
                copied+=len(chunk)
                if copied>member.file_size:
                    raise ValueError('Member exceeded its declared size')
                digest.update(chunk)
                output.write(chunk)
            output.flush()
        if copied!=member.file_size:
            raise ValueError('Member size mismatch')
        with target.open('rb') as stream:
            identity=describe_header(stream)
        zip_digest=hashlib.file_digest(source.open('rb'),'sha256').hexdigest()
        after=source.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
            raise ValueError('Input ZIP changed while staging')
        record={'source_zip':str(source),'source_zip_bytes':before.st_size,'source_zip_sha256':zip_digest,
                'member':member.filename,'zip_crc32':f'{member.CRC:08x}','zip_crc_verified_by_zipfile':True,
                'staged_image':str(target),'image_bytes':copied,'image_sha256':digest.hexdigest(),
                'source_zip_unchanged':True,'identity':identity,'elapsed_seconds':round(time.monotonic()-started,3),
                'execution':'python_standard_library_archive_staging','game_executed':False}
        (destination/'source-identity.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
        print(json.dumps(record,indent=2),flush=True)


if __name__=='__main__':
    main()
