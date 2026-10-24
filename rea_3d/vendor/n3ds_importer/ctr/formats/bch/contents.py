from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .tree import names as tree_names

DICT_SIZE = 12

SECTION_NAMES = (
    "models",
    "materials",
    "shaders",
    "textures",
    "luts",
    "lights",
    "cameras",
    "fogs",
    "skeletal_animations",
    "material_animations",
    "visibility_animations",
    "light_animations",
    "camera_animations",
    "fog_animations",
    "scenes",
)


@dataclass
class Dict_:
    name: str = ""
    values: int = 0
    count: int = 0
    tree: int = 0
    entries: list = field(default_factory=list)

    def __len__(self) -> int:
        return self.count

    def __bool__(self) -> bool:
        return self.count > 0


@dataclass
class Contents:
    dicts: dict = field(default_factory=dict)

    def __getattr__(self, name):
        try:
            return self.dicts[name]
        except KeyError:
            raise AttributeError(name)


def read_contents(data, header) -> Contents:
    contents = Contents()
    for index, name in enumerate(SECTION_NAMES):
        offset = header.contents_address + index * DICT_SIZE
        if offset + DICT_SIZE > len(data):
            break
        values, count, tree = struct.unpack_from("<III", data, offset)
        entries = tree_names(data, tree) if tree and count else []
        contents.dicts[name] = Dict_(
            name=name, values=values, count=count, tree=tree, entries=entries)
    return contents
