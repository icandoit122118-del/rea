from __future__ import annotations

from dataclasses import dataclass, field

from ...binstream import BinStream
from .section import ALIGNMENT, Section

NAME_FIELD = 0x40
HASH_NAME_SIZE = 4 + NAME_FIELD
COLOR_COUNT = 12

WRAP_MODES = {0: "CLAMP_TO_EDGE", 1: "CLAMP_TO_BORDER", 2: "REPEAT", 3: "MIRRORED_REPEAT"}

@dataclass
class TextureCoord:
    name: str = ""
    unit: int = 0
    mapping_type: int = 0
    scale: tuple = (1.0, 1.0)
    rotation: float = 0.0
    translation: tuple = (0.0, 0.0)
    wrap_u: int = 0
    wrap_v: int = 0

@dataclass
class Material:
    name: str = ""
    shader_name: str = ""
    vertex_shader_name: str = ""
    fragment_shader_name: str = ""
    bump_texture: int = -1
    diffuse: tuple = (1.0, 1.0, 1.0, 1.0)
    ambient: tuple = (0.0, 0.0, 0.0, 1.0)
    emission: tuple = (0.0, 0.0, 0.0, 1.0)
    coords: list = field(default_factory=list)

    @property
    def texture_names(self) -> list:
        return [c.name for c in self.coords if c.name]

def _hash_name(stream: BinStream) -> str:
    stream.u32()
    return stream.bstring()

def _rgba(stream: BinStream) -> tuple:
    return tuple(stream.u8() / 255.0 for _ in range(4))

def _read_texture_coord(stream: BinStream) -> TextureCoord:
    coord = TextureCoord(name=_hash_name(stream))
    coord.unit = stream.u8()
    coord.mapping_type = stream.u8()
    coord.scale = (stream.f32(), stream.f32())
    coord.rotation = stream.f32()
    coord.translation = (stream.f32(), stream.f32())
    coord.wrap_u = stream.u32()
    coord.wrap_v = stream.u32()
    stream.u32()
    stream.u32()
    stream.u32()
    return coord

def read_material(stream: BinStream, section: Section) -> Material:
    body_start = stream.tell()

    material = Material(
        name=_hash_name(stream),
        shader_name=_hash_name(stream),
        vertex_shader_name=_hash_name(stream),
        fragment_shader_name=_hash_name(stream),
    )

    stream.skip(12)
    stream.u32()

    material.bump_texture = stream.i8()
    stream.skip(6)
    stream.u8()

    colors = [_rgba(stream) for _ in range(COLOR_COUNT)]
    material.emission = colors[9]
    material.ambient = colors[10]
    material.diffuse = colors[11]

    stream.skip(4 * 4)
    stream.skip(4 * 4)
    stream.skip(4 * 2)
    stream.skip(4 * 9)
    stream.skip(4)
    stream.skip(4 * 4)

    units = stream.u32()
    for _ in range(min(units, 3)):
        material.coords.append(_read_texture_coord(stream))

    stream.seek(body_start + section.length)
    return material

def read_materials(stream: BinStream, expected: int) -> list:
    from .section import read_section

    materials = []
    for _ in range(expected):
        start = stream.tell()
        if start % ALIGNMENT:
            stream.align(ALIGNMENT)
        section = read_section(stream)
        if section.magic != "material":
            stream.seek(start)
            break
        materials.append(read_material(stream, section))
    return materials
