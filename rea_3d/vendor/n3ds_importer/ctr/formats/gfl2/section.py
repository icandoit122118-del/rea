from __future__ import annotations

from dataclasses import dataclass

from ...binstream import BinStream

ALIGNMENT = 0x10
HEADER_SIZE = 0x10

@dataclass(frozen=True)
class Section:
    magic: str
    length: int

def read_section(stream: BinStream) -> Section:
    magic = stream.pstring(8)
    length = stream.u32()
    stream.u32()
    return Section(magic, length)

def expect_section(stream: BinStream, magic: str) -> Section:
    section = read_section(stream)
    if section.magic != magic:
        raise ValueError(f"expected a {magic!r} section, found {section.magic!r}")
    return section
