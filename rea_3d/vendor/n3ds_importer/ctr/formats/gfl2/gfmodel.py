from __future__ import annotations

from dataclasses import dataclass, field

from ...binstream import BinStream
from .section import ALIGNMENT, expect_section, read_section
from .gfmaterial import read_materials
from .gfmesh import Mesh, read_mesh

MAGIC = 0x15122117
MAGIC_BYTES = bytes([0x17, 0x21, 0x12, 0x15])
SECTION_MAGIC = "gfmodel"
NAME_FIELD = 0x40
MESH_SECTION_MAGIC = b"mesh\x00\x00\x00\x00"
MATERIAL_SECTION_MAGIC = b"material"

@dataclass(frozen=True)
class HashName:
    hash: int
    name: str

@dataclass(frozen=True)
class Bone:
    name: str
    parent: str
    flags: int
    scale: tuple
    rotation: tuple
    translation: tuple

@dataclass
class Model:
    name: str = ""
    bbox_min: tuple = ()
    bbox_max: tuple = ()
    shader_names: list = field(default_factory=list)
    texture_names: list = field(default_factory=list)
    material_names: list = field(default_factory=list)
    mesh_names: list = field(default_factory=list)
    bones: list = field(default_factory=list)
    materials: list = field(default_factory=list)
    meshes: list = field(default_factory=list)

def sniff_gfmodel(head: bytes) -> bool:
    return head[:4] == MAGIC_BYTES and head[0x10:0x17] == b"gfmodel"

def _read_hash_table(stream: BinStream) -> list:
    count = stream.u32()
    return [HashName(stream.u32(), stream.pstring(NAME_FIELD)) for _ in range(count)]

def _read_bone(stream: BinStream) -> Bone:
    return Bone(
        name=stream.bstring(),
        parent=stream.bstring(),
        flags=stream.u8(),
        scale=stream.vec(3),
        rotation=stream.vec(3),
        translation=stream.vec(3),
    )

def read_gfmodel(stream: BinStream, name: str = "") -> Model:
    magic = stream.u32()
    if magic != MAGIC:
        raise ValueError(f"not a GFModel: magic {magic:#010x}")
    stream.u32()
    stream.align(ALIGNMENT)

    expect_section(stream, SECTION_MAGIC)

    model = Model(name=name)
    model.shader_names = _read_hash_table(stream)
    model.texture_names = _read_hash_table(stream)
    model.material_names = _read_hash_table(stream)
    model.mesh_names = _read_hash_table(stream)

    model.bbox_min = stream.vec(4)
    model.bbox_max = stream.vec(4)
    stream.skip(64)

    unknown_length = stream.u32()
    unknown_offset = stream.u32()
    stream.skip(8)
    stream.skip(unknown_offset + unknown_length)

    bone_count = stream.i32()
    stream.skip(0xC)
    model.bones = [_read_bone(stream) for _ in range(bone_count)]

    materials_at = _find_section(stream, MATERIAL_SECTION_MAGIC)
    if materials_at is not None:
        stream.seek(materials_at)
        model.materials = read_materials(stream, len(model.material_names))

    model.meshes = _read_meshes(stream, len(model.mesh_names))
    return model

def _find_section(stream: BinStream, magic: bytes) -> int | None:
    data = stream.slice(0, len(stream))
    probe = stream.tell()
    while True:
        probe = data.find(magic, probe)
        if probe == -1:
            return None
        if probe % ALIGNMENT == 0:
            return probe
        probe += 1

def _read_meshes(stream: BinStream, expected: int) -> list:
    if expected == 0:
        return []

    found = _find_section(stream, MESH_SECTION_MAGIC)
    if found is None:
        raise ValueError(
            f"model declares {expected} meshes but no aligned 'mesh' section "
            f"was found after offset {stream.tell():#x}"
        )

    stream.seek(found)
    meshes = []
    for _ in range(expected):
        section_start = stream.tell()
        section = read_section(stream)
        if section.magic != "mesh":
            break
        body_start = stream.tell()
        meshes.append(read_mesh(stream, section))
        stream.seek(body_start + section.length)
        _ = section_start
    return meshes
