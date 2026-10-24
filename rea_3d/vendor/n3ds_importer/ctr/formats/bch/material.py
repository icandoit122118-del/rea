from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .tree import read_cstring

MATERIAL_SIZE = 0x58
TEXTURE0_AT = 0x48
TEXTURE1_AT = 0x4C
TEXTURE2_AT = 0x50
NAME_AT = 0x54
TEXTURE_SLOTS = 3


@dataclass
class Material:
    name: str = ""
    textures: list = field(default_factory=list)

    @property
    def base_color_texture(self) -> str:
        return self.textures[0] if self.textures else ""

    @property
    def normal_texture(self) -> str:
        for texture in self.textures:
            lowered = texture.lower()
            if lowered.endswith(("nor", "nrm", "_n")) or "nor." in lowered:
                return texture
        return ""


def read_material(data, at: int) -> Material:
    material = Material(name=read_cstring(data, _pointer(data, at + NAME_AT)))
    for index in range(TEXTURE_SLOTS):
        name = read_cstring(data, _pointer(data, at + TEXTURE0_AT + index * 4))
        if name:
            material.textures.append(name)
    return material


def _pointer(data, at: int) -> int:
    if at + 4 > len(data):
        return 0
    (value,) = struct.unpack_from("<I", data, at)
    return value


def read_materials(data, at: int, count: int) -> list:
    materials = []
    for index in range(count):
        offset = at + index * MATERIAL_SIZE
        if offset + MATERIAL_SIZE > len(data):
            break
        materials.append(read_material(data, offset))
    return materials
