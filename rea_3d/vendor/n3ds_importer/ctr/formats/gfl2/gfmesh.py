from __future__ import annotations

from dataclasses import dataclass, field

from ...binstream import BinStream
from ...pica import commands as reg
from ...pica.attributes import (
    FORMAT_SCALE,
    Attribute,
    AttributeFormat,
    AttributeName,
    FixedAttribute,
    decode_vertices,
)
from .section import Section

BONE_INDEX_SLOTS = 0x1F
NAME_FIELD = 0x40

@dataclass
class SubMesh:
    name: str = ""
    bone_indices: list = field(default_factory=list)
    vertex_stride: int = 0
    attributes: list = field(default_factory=list)
    fixed_attributes: list = field(default_factory=list)
    raw_buffer: bytes = b""
    indices: list = field(default_factory=list)
    primitive_mode: int = 0

    @property
    def vertex_count(self) -> int:
        return len(self.raw_buffer) // self.vertex_stride if self.vertex_stride else 0

    def vertices(self) -> dict:
        return decode_vertices(
            self.raw_buffer, self.vertex_stride, self.attributes, self.vertex_count
        )

@dataclass
class Mesh:
    name: str = ""
    bone_indices_per_vertex: int = 0
    submeshes: list = field(default_factory=list)

def _float24(word0: int, word1: int, word2: int) -> tuple:
    bits = (word0 << 64) | (word1 << 32) | word2
    out = []
    for i in range(4):
        raw = (bits >> (72 - i * 24)) & 0xFFFFFF
        sign = (raw >> 23) & 1
        exponent = (raw >> 16) & 0x7F
        mantissa = raw & 0xFFFF
        if exponent == 0 and mantissa == 0:
            value = 0.0
        else:
            value = (1.0 + mantissa / 65536.0) * (2.0 ** (exponent - 63))
        out.append(-value if sign else value)
    return tuple(out)

def _read_command_blocks(stream: BinStream) -> list:
    blocks = []
    while True:
        length = stream.u32()
        index = stream.u32()
        count = stream.u32()
        stream.u32()
        words = [stream.u32() for _ in range(length >> 2)]
        blocks.append(words)
        if index >= count - 1:
            break
    return blocks

def _attributes_from_commands(enable_words: list, submesh: SubMesh) -> None:
    formats = 0
    buffer_attributes = 0
    permutation = 0
    total = 0
    fixed_index = 0
    fixed = {}

    for cmd in reg.decode_commands(enable_words):
        p = cmd.param
        r = cmd.register
        if r == reg.ATTRIBBUFFERS_FORMAT_LOW:
            formats |= p
        elif r == reg.ATTRIBBUFFERS_FORMAT_HIGH:
            formats |= p << 32
        elif r == reg.ATTRIBBUFFER0_CONFIG1:
            buffer_attributes |= p
        elif r == reg.ATTRIBBUFFER0_CONFIG2:
            buffer_attributes |= (p & 0xFFFF) << 32
            submesh.vertex_stride = (p >> 16) & 0xFF
        elif r == reg.FIXEDATTRIB_INDEX:
            fixed_index = p
        elif r in (reg.FIXEDATTRIB_DATA0, reg.FIXEDATTRIB_DATA1, reg.FIXEDATTRIB_DATA2):
            slot = fixed.setdefault(fixed_index, [0, 0, 0])
            slot[r - reg.FIXEDATTRIB_DATA0] = p
        elif r == reg.VSH_NUM_ATTR:
            total = p + 1
        elif r == reg.VSH_ATTRIBUTES_PERMUTATION_LOW:
            permutation |= p
        elif r == reg.VSH_ATTRIBUTES_PERMUTATION_HIGH:
            permutation |= p << 32

    for index in range(total):
        if (formats >> (48 + index)) & 1:
            name = AttributeName((permutation >> (index * 4)) & 0xF)
            words = fixed.get(index, [0, 0, 0])
            value = _float24(*words)
            if name in (AttributeName.Color, AttributeName.BoneWeight):
                value = tuple(v / 255.0 for v in value)
            submesh.fixed_attributes.append(FixedAttribute(name, value))
        else:
            permutation_idx = (buffer_attributes >> (index * 4)) & 0xF
            name_bits = (permutation >> (permutation_idx * 4)) & 0xF
            fmt_bits = (formats >> (permutation_idx * 4)) & 0xF
            fmt = AttributeFormat(fmt_bits & 3)
            name = AttributeName(name_bits)
            scale = 1.0 if name is AttributeName.BoneIndex else FORMAT_SCALE[fmt_bits & 3]
            submesh.attributes.append(
                Attribute(name, fmt, (fmt_bits >> 2) + 1, scale)
            )

def _index_info_from_commands(index_words: list, submesh: SubMesh) -> tuple:
    buffer_address = 0
    buffer_count = 0
    for cmd in reg.decode_commands(index_words):
        if cmd.register == reg.INDEXBUFFER_CONFIG:
            buffer_address = cmd.param
        elif cmd.register == reg.NUMVERTICES:
            buffer_count = cmd.param
        elif cmd.register == reg.PRIMITIVE_CONFIG:
            submesh.primitive_mode = cmd.param >> 8
    return buffer_address, buffer_count

def read_mesh(stream: BinStream, section: Section) -> Mesh:
    mesh = Mesh()
    stream.u32()
    mesh.name = stream.pstring(NAME_FIELD)
    stream.u32()
    stream.skip(32)
    submesh_count = stream.u32()
    mesh.bone_indices_per_vertex = stream.i32()
    stream.skip(0x10)

    blocks = _read_command_blocks(stream)

    sizes = []
    for _ in range(submesh_count):
        sm = SubMesh()
        stream.u32()
        sm.name = stream.istring()
        bone_count = stream.u8()
        raw_indices = [stream.u8() for _ in range(BONE_INDEX_SLOTS)]
        sm.bone_indices = raw_indices[:bone_count]
        sizes.append({
            "vertices_count": stream.i32(),
            "indices_count": stream.i32(),
            "vertices_length": stream.i32(),
            "indices_length": stream.i32(),
        })
        mesh.submeshes.append(sm)

    for i, sm in enumerate(mesh.submeshes):
        enable_words = blocks[i * 3 + 0]
        index_words = blocks[i * 3 + 2]

        _attributes_from_commands(enable_words, sm)
        buffer_address, buffer_count = _index_info_from_commands(index_words, sm)

        sm.raw_buffer = stream.bytes(sizes[i]["vertices_length"])

        index_start = stream.tell()
        wide = (buffer_address >> 31) != 0
        sm.indices = [stream.u16() if wide else stream.u8() for _ in range(buffer_count)]
        stream.seek(index_start + sizes[i]["indices_length"])

    _ = section
    return mesh
