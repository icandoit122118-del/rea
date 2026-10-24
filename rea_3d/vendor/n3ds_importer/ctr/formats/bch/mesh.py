from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ...pica import commands as reg
from ...pica.attributes import (
    FORMAT_SCALE,
    Attribute,
    AttributeFormat,
    AttributeName,
    FixedAttribute,
    decode_vertices,
)

MESH_SIZE = 56
MATERIAL_AT = 0x00
NODE_AT = 0x04
ENABLE_AT = 0x08
SUBMESH_AT = 0x10
DISABLE_AT = 0x18
CENTER_AT = 0x20

SUBMESH_SIZE = 52
BONE_SLOTS = 20
SUBMESH_BONES_AT = 0x04
SUBMESH_COMMANDS_AT = 0x2C

ATTRIBBUFFERS_LOC = 0x0200
ATTRIBBUFFER_ADDRESS = 0x0203

BONE_WEIGHT_SCALE = 0.01


@dataclass
class SubMesh:
    bone_indices: list = field(default_factory=list)
    indices: list = field(default_factory=list)
    primitive_mode: int = 0


@dataclass
class Mesh:
    material_index: int = 0
    node_index: int = 0
    center: tuple = (0.0, 0.0, 0.0)
    vertex_stride: int = 0
    buffer_at: int = 0
    attributes: list = field(default_factory=list)
    fixed_attributes: list = field(default_factory=list)
    submeshes: list = field(default_factory=list)
    raw_buffer: bytes = b""

    @property
    def vertex_count(self) -> int:
        return len(self.raw_buffer) // self.vertex_stride if self.vertex_stride else 0

    def vertices(self) -> dict:
        return decode_vertices(
            self.raw_buffer, self.vertex_stride, self.attributes, self.vertex_count)


def _words(data, at: int, count: int) -> list:
    if not at or at + count * 4 > len(data):
        return []
    return list(struct.unpack_from("<%dI" % count, data, at))


def _attributes_from_commands(words: list, mesh: Mesh) -> None:
    formats = 0
    buffer_attributes = 0
    permutation = 0
    total = 0
    fixed_index = 0
    fixed: dict = {}

    for cmd in reg.decode_commands(words):
        p = cmd.param
        r = cmd.register
        if r == ATTRIBBUFFER_ADDRESS:
            mesh.buffer_at = p
        elif r == reg.ATTRIBBUFFERS_FORMAT_LOW:
            formats |= p
        elif r == reg.ATTRIBBUFFERS_FORMAT_HIGH:
            formats |= p << 32
        elif r == reg.ATTRIBBUFFER0_CONFIG1:
            buffer_attributes |= p
        elif r == reg.ATTRIBBUFFER0_CONFIG2:
            buffer_attributes |= (p & 0xFFFF) << 32
            mesh.vertex_stride = (p >> 16) & 0xFF
        elif r == reg.FIXEDATTRIB_INDEX:
            fixed_index = p
        elif r in (reg.FIXEDATTRIB_DATA0, reg.FIXEDATTRIB_DATA1, reg.FIXEDATTRIB_DATA2):
            fixed.setdefault(fixed_index, [0, 0, 0])[r - reg.FIXEDATTRIB_DATA0] = p
        elif r == reg.VSH_NUM_ATTR:
            total = p + 1
        elif r == reg.VSH_ATTRIBUTES_PERMUTATION_LOW:
            permutation |= p
        elif r == reg.VSH_ATTRIBUTES_PERMUTATION_HIGH:
            permutation |= p << 32

    for index in range(total):
        if (formats >> (48 + index)) & 1:
            name = AttributeName((permutation >> (index * 4)) & 0xF)
            mesh.fixed_attributes.append(FixedAttribute(name, (0.0, 0.0, 0.0, 0.0)))
            continue
        permutation_index = (buffer_attributes >> (index * 4)) & 0xF
        name_bits = (permutation >> (permutation_index * 4)) & 0xF
        format_bits = (formats >> (permutation_index * 4)) & 0xF
        fmt = AttributeFormat(format_bits & 3)
        name = AttributeName(name_bits)
        if name is AttributeName.BoneIndex:
            scale = 1.0
        elif name is AttributeName.BoneWeight:
            scale = BONE_WEIGHT_SCALE
        else:
            scale = FORMAT_SCALE[format_bits & 3]
        mesh.attributes.append(Attribute(name, fmt, (format_bits >> 2) + 1, scale))


def read_submesh(data, at: int) -> SubMesh:
    (bone_count,) = struct.unpack_from("<H", data, at + 2)
    raw = struct.unpack_from("<%dH" % BONE_SLOTS, data, at + SUBMESH_BONES_AT)
    submesh = SubMesh(bone_indices=list(raw[:min(bone_count, BONE_SLOTS)]))

    commands_at, commands_count = struct.unpack_from(
        "<II", data, at + SUBMESH_COMMANDS_AT)
    buffer_address = 0
    buffer_count = 0
    for cmd in reg.decode_commands(_words(data, commands_at, commands_count)):
        if cmd.register == reg.INDEXBUFFER_CONFIG:
            buffer_address = cmd.param
        elif cmd.register == reg.NUMVERTICES:
            buffer_count = cmd.param
        elif cmd.register == reg.PRIMITIVE_CONFIG:
            submesh.primitive_mode = cmd.param >> 8

    wide = (buffer_address >> 31) != 0
    start = buffer_address & 0x7FFFFFFF
    if start and buffer_count:
        size = 2 if wide else 1
        if start + buffer_count * size <= len(data):
            fmt = "<%d%s" % (buffer_count, "H" if wide else "B")
            submesh.indices = list(struct.unpack_from(fmt, data, start))

    return submesh


def read_mesh(data, at: int) -> Mesh:
    mesh = Mesh()
    (mesh.material_index,) = struct.unpack_from("<H", data, at + MATERIAL_AT)
    (mesh.node_index,) = struct.unpack_from("<H", data, at + NODE_AT)
    mesh.center = struct.unpack_from("<3f", data, at + CENTER_AT)

    enable_at, enable_count = struct.unpack_from("<II", data, at + ENABLE_AT)
    _attributes_from_commands(_words(data, enable_at, enable_count), mesh)

    submesh_at, submesh_count = struct.unpack_from("<II", data, at + SUBMESH_AT)
    for index in range(submesh_count):
        offset = submesh_at + index * SUBMESH_SIZE
        if offset + SUBMESH_SIZE > len(data):
            break
        mesh.submeshes.append(read_submesh(data, offset))

    highest = 0
    for submesh in mesh.submeshes:
        if submesh.indices:
            highest = max(highest, max(submesh.indices))
    if mesh.vertex_stride and mesh.buffer_at:
        length = (highest + 1) * mesh.vertex_stride
        if mesh.buffer_at + length <= len(data):
            mesh.raw_buffer = bytes(data[mesh.buffer_at:mesh.buffer_at + length])

    return mesh


def read_meshes(data, model) -> list:
    meshes = []
    for index in range(model.mesh_count):
        at = model.meshes_at + index * MESH_SIZE
        if at + MESH_SIZE > len(data):
            break
        meshes.append(read_mesh(data, at))
    return meshes
