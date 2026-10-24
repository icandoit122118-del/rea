from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .material import read_materials as _read_materials
from .tree import names as tree_names
from .tree import read_cstring

MATERIALS_AT = 0x34
MESHES_AT = 0x40
SKELETON_AT = 0x70

BONE_SIZE = 100
BONE_NAME_AT = 0x5C

DICT_SIZE = 12


@dataclass
class Bone:
    name: str = ""
    parent: int = -1
    flags: int = 0
    scale: tuple = (1.0, 1.0, 1.0)
    rotation: tuple = (0.0, 0.0, 0.0)
    translation: tuple = (0.0, 0.0, 0.0)


@dataclass
class Model:
    name: str = ""
    bones: list = field(default_factory=list)
    material_names: list = field(default_factory=list)
    materials: list = field(default_factory=list)
    mesh_count: int = 0
    meshes_at: int = 0


def _dict(data, at: int) -> tuple:
    values, count, tree = struct.unpack_from("<III", data, at)
    return values, count, tree


def read_bone(data, at: int) -> Bone:
    flags, parent = struct.unpack_from("<Ih", data, at)
    scale = struct.unpack_from("<3f", data, at + 0x08)
    rotation = struct.unpack_from("<3f", data, at + 0x14)
    translation = struct.unpack_from("<3f", data, at + 0x20)
    (name_at,) = struct.unpack_from("<I", data, at + BONE_NAME_AT)
    return Bone(
        name=read_cstring(data, name_at),
        parent=parent,
        flags=flags,
        scale=scale,
        rotation=rotation,
        translation=translation,
    )


def read_bones(data, at: int, count: int) -> list:
    bones = []
    for index in range(count):
        offset = at + index * BONE_SIZE
        if offset + BONE_SIZE > len(data):
            break
        bones.append(read_bone(data, offset))
    return bones


def read_model(data, at: int, name: str = "") -> Model:
    model = Model(name=name)

    materials_at, material_count, material_tree = _dict(data, at + MATERIALS_AT)
    if material_count and material_tree:
        model.material_names = tree_names(data, material_tree)
    if material_count and materials_at:
        model.materials = _read_materials(data, materials_at, material_count)

    meshes_at, mesh_count = struct.unpack_from("<II", data, at + MESHES_AT)
    model.meshes_at = meshes_at
    model.mesh_count = mesh_count

    skeleton_at, bone_count, _ = _dict(data, at + SKELETON_AT)
    if bone_count and skeleton_at:
        model.bones = read_bones(data, skeleton_at, bone_count)

    return model


def read_models(data, contents) -> list:
    models = []
    entry = contents.models
    for index in range(entry.count):
        pointer_at = entry.values + index * 4
        if pointer_at + 4 > len(data):
            break
        (model_at,) = struct.unpack_from("<I", data, pointer_at)
        if not model_at:
            continue
        name = entry.entries[index] if index < len(entry.entries) else ""
        models.append(read_model(data, model_at, name))
    return models
