from __future__ import annotations

import struct
from dataclasses import dataclass

from ...binstream import BinStream

MAGIC = b"BCH\x00"

CONTENTS = 0
STRINGS = 1
COMMANDS = 2
COMMANDS_SRC = 3
RAW_DATA = 4
RAW_DATA_TEXTURE = 5
RAW_DATA_VERTEX = 6
RAW_DATA_INDEX16 = 7
RAW_DATA_INDEX8 = 8
RAW_EXT = 9
RAW_EXT_TEXTURE = 10
RAW_EXT_VERTEX = 11
RAW_EXT_INDEX16 = 12
RAW_EXT_INDEX8 = 13

_INDEX16_SECTIONS = (RAW_DATA_INDEX16, RAW_EXT_INDEX16)
_RAW_EXT_SECTIONS = (RAW_EXT, RAW_EXT_TEXTURE, RAW_EXT_VERTEX,
                     RAW_EXT_INDEX16, RAW_EXT_INDEX8)
_RAW_DATA_SECTIONS = (RAW_DATA, RAW_DATA_TEXTURE, RAW_DATA_VERTEX,
                      RAW_DATA_INDEX16, RAW_DATA_INDEX8)

POINTER_MASK = 0x1FFFFFF


@dataclass
class Header:
    backward: int = 0
    forward: int = 0
    converter: int = 0
    contents_address: int = 0
    strings_address: int = 0
    commands_address: int = 0
    raw_data_address: int = 0
    raw_ext_address: int = 0
    relocation_address: int = 0
    contents_length: int = 0
    strings_length: int = 0
    commands_length: int = 0
    raw_data_length: int = 0
    raw_ext_length: int = 0
    relocation_length: int = 0
    uninit_data_length: int = 0
    uninit_commands_length: int = 0
    flags: int = 0
    address_count: int = 0


def sniff_bch(head: bytes) -> bool:
    return head[:4] == MAGIC


def read_header(stream: BinStream) -> Header:
    magic = stream.bytes(4)
    if magic != MAGIC:
        raise ValueError(f"not a BCH file: magic {magic!r}")

    header = Header()
    header.backward = stream.u8()
    header.forward = stream.u8()
    header.converter = stream.u16()

    has_raw_ext = header.backward >= 0x21

    header.contents_address = stream.i32()
    header.strings_address = stream.i32()
    header.commands_address = stream.i32()
    header.raw_data_address = stream.i32()
    if has_raw_ext:
        header.raw_ext_address = stream.i32()
    header.relocation_address = stream.i32()

    header.contents_length = stream.i32()
    header.strings_length = stream.i32()
    header.commands_length = stream.i32()
    header.raw_data_length = stream.i32()
    if has_raw_ext:
        header.raw_ext_length = stream.i32()
    header.relocation_length = stream.i32()

    header.uninit_data_length = stream.i32()
    header.uninit_commands_length = stream.i32()

    if header.backward >= 8:
        header.flags = stream.u8()
        stream.skip(1)
        header.address_count = stream.u16()

    return header


def section_base(header: Header, section: int) -> int:
    if section in (CONTENTS,):
        return header.contents_address
    if section in (STRINGS,):
        return header.strings_address
    if section in (COMMANDS, COMMANDS_SRC):
        return header.commands_address
    if section in _RAW_DATA_SECTIONS:
        return header.raw_data_address
    if section in _RAW_EXT_SECTIONS:
        return header.raw_ext_address
    return 0


def legacy_reloc_diff(section: int, backward: int) -> int:
    if 7 < backward < 0x21 and section >= RAW_DATA_VERTEX:
        return -1
    if backward < 7 and section >= COMMANDS_SRC:
        return 1
    return 0


def relocate(data: bytes, header: Header) -> bytearray:
    out = bytearray(data)
    end = header.relocation_address + header.relocation_length

    for offset in range(header.relocation_address, end, 4):
        (value,) = struct.unpack_from("<I", out, offset)
        pointer = value & POINTER_MASK
        target = (value >> 25) & 0xF
        source = value >> 29

        target += legacy_reloc_diff(target, header.backward)

        if target != STRINGS:
            pointer <<= 2

        at = section_base(header, source) + pointer
        if at + 4 > len(out):
            continue

        base = section_base(header, target)
        if target in _INDEX16_SECTIONS:
            base |= 1 << 31

        (current,) = struct.unpack_from("<I", out, at)
        struct.pack_into("<I", out, at, (current + base) & 0xFFFFFFFF)

    return out
