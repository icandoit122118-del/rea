from __future__ import annotations

from dataclasses import dataclass, field

@dataclass
class Bone:
    name: str
    parent: str | None
    translation: tuple = (0.0, 0.0, 0.0)
    rotation: tuple = (0.0, 0.0, 0.0)
    scale: tuple = (1.0, 1.0, 1.0)

@dataclass
class SubMesh:
    name: str = ""
    material: str = ""
    positions: list = field(default_factory=list)
    normals: list = field(default_factory=list)
    tangents: list = field(default_factory=list)
    colors: list = field(default_factory=list)
    uv0: list = field(default_factory=list)
    uv1: list = field(default_factory=list)
    uv2: list = field(default_factory=list)
    bones: list = field(default_factory=list)
    weights: list = field(default_factory=list)
    triangles: list = field(default_factory=list)

    @property
    def vertex_count(self) -> int:
        return len(self.positions)

@dataclass
class TextureSlot:
    name: str = ""
    unit: int = 0
    scale: tuple = (1.0, 1.0)
    rotation: float = 0.0
    translation: tuple = (0.0, 0.0)
    wrap_u: int = 2
    wrap_v: int = 2

@dataclass
class Material:
    name: str = ""
    diffuse: tuple = (1.0, 1.0, 1.0, 1.0)
    ambient: tuple = (0.0, 0.0, 0.0, 1.0)
    emission: tuple = (0.0, 0.0, 0.0, 1.0)
    textures: list = field(default_factory=list)

    @property
    def base_color_texture(self) -> str:
        return self.textures[0].name if self.textures else ""

    @property
    def normal_texture(self) -> str:
        for slot in self.textures:
            if slot.name.lower().endswith(("nor.tga", "nrm.tga", "_nor", "_nrm")):
                return slot.name
        return ""

@dataclass
class Texture:
    name: str = ""
    width: int = 0
    height: int = 0
    rgba: bytes = b""

@dataclass
class Model:
    name: str = ""
    bones: list = field(default_factory=list)
    submeshes: list = field(default_factory=list)
    materials: list = field(default_factory=list)
    texture_names: list = field(default_factory=list)
    material_names: list = field(default_factory=list)

@dataclass
class Scene:
    models: list = field(default_factory=list)
    textures: list = field(default_factory=list)
    animations: list = field(default_factory=list)
