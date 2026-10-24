from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum

class AttributeName(IntEnum):
    Position = 0
    Normal = 1
    Tangent = 2
    Color = 3
    TexCoord0 = 4
    TexCoord1 = 5
    TexCoord2 = 6
    BoneIndex = 7
    BoneWeight = 8
    UserAttribute0 = 9
    Interleave = 21

class AttributeFormat(IntEnum):
    Byte = 0
    Ubyte = 1
    Short = 2
    Float = 3

FORMAT_SCALE = (1.0 / 127, 1.0 / 255, 1.0 / 32767, 1.0)

_FORMAT_STRUCT = {
    AttributeFormat.Byte: struct.Struct("<b"),
    AttributeFormat.Ubyte: struct.Struct("<B"),
    AttributeFormat.Short: struct.Struct("<h"),
    AttributeFormat.Float: struct.Struct("<f"),
}

VERTEX_ALIGNMENT = 4

FORMAT_SIZE = {
    AttributeFormat.Byte: 1,
    AttributeFormat.Ubyte: 1,
    AttributeFormat.Short: 2,
    AttributeFormat.Float: 4,
}

@dataclass(frozen=True)
class Attribute:
    name: AttributeName
    fmt: AttributeFormat
    elements: int
    scale: float

    @property
    def size(self) -> int:
        return FORMAT_SIZE[self.fmt] * self.elements


@dataclass(frozen=True)
class FixedAttribute:
    name: AttributeName
    value: tuple

def decode_vertices(raw: bytes, stride: int, attributes: list, count: int) -> dict:
    if stride <= 0:
        raise ValueError(f"vertex stride must be positive, got {stride}")

    out = {attr.name: [] for attr in attributes}

    for v in range(count):
        base = v * stride
        if base + stride > len(raw):
            raise ValueError(
                f"vertex {v} runs past the buffer ({len(raw)} bytes, stride {stride})"
            )
        offset = base
        for attr in attributes:
            reader = _FORMAT_STRUCT[attr.fmt]
            values = []
            for e in range(attr.elements):
                (value,) = reader.unpack_from(raw, offset + e * reader.size)
                values.append(value)
            offset += attr.size

            if attr.name is AttributeName.BoneIndex:
                out[attr.name].append(tuple(int(x) for x in values))
            elif attr.scale == 1.0:
                out[attr.name].append(tuple(float(x) for x in values))
            else:
                out[attr.name].append(tuple(float(x) * attr.scale for x in values))

    return out
