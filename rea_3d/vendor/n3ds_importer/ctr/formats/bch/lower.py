from __future__ import annotations

from ... import ir
from ...binstream import BinStream
from ...pica.attributes import AttributeName
from ..gfl2.lower import _triangles
from .animation import read_animations
from .contents import read_contents
from .header import read_header, relocate
from .texture import read_textures
from .material import Material as RawMaterial
from .mesh import read_meshes
from .model import read_models


def lower_bch(blob: bytes) -> list:
    header = read_header(BinStream(blob))
    data = relocate(blob, header)
    contents = read_contents(data, header)

    models = []
    for raw in read_models(data, contents):
        models.append(_lower_model(data, raw))
    return models


def lower_bch_textures(blob: bytes) -> list:
    try:
        header = read_header(BinStream(blob))
        data = relocate(blob, header)
        contents = read_contents(data, header)
    except Exception:
        return []

    out = []
    for texture in read_textures(data, contents):
        try:
            rgba = bytes(texture.to_rgba())
        except Exception:
            continue
        out.append(ir.Texture(name=texture.name, width=texture.width,
                              height=texture.height, rgba=rgba))
    return out


def lower_bch_animations(blob: bytes) -> list:
    try:
        header = read_header(BinStream(blob))
        data = relocate(blob, header)
        contents = read_contents(data, header)
        return read_animations(data, contents, header.backward)
    except Exception:
        return []


def _lower_model(data, raw) -> ir.Model:
    model = ir.Model(name=raw.name)

    for bone in raw.bones:
        parent = None
        if 0 <= bone.parent < len(raw.bones):
            parent = raw.bones[bone.parent].name
        model.bones.append(ir.Bone(
            name=bone.name,
            parent=parent,
            translation=bone.translation,
            rotation=bone.rotation,
            scale=bone.scale,
        ))

    for material in raw.materials:
        model.materials.append(_lower_material(material))
    model.material_names = [m.name for m in raw.materials]
    model.texture_names = sorted({
        texture for material in raw.materials for texture in material.textures})

    for mesh in read_meshes(data, raw):
        material_name = ""
        if mesh.material_index < len(raw.materials):
            material_name = raw.materials[mesh.material_index].name
        model.submeshes.extend(_lower_mesh(mesh, material_name))

    return model


def _lower_material(raw: RawMaterial) -> ir.Material:
    return ir.Material(
        name=raw.name,
        textures=[
            ir.TextureSlot(name=name, unit=index)
            for index, name in enumerate(raw.textures)
        ],
    )


def _lower_mesh(mesh, material_name: str) -> list:
    if not mesh.attributes or not mesh.vertex_count:
        return []

    data = mesh.vertices()
    positions = data.get(AttributeName.Position, [])
    if not positions:
        return []

    normals = data.get(AttributeName.Normal, [])
    tangents = data.get(AttributeName.Tangent, [])
    colors = data.get(AttributeName.Color, [])
    uv0 = data.get(AttributeName.TexCoord0, [])
    uv1 = data.get(AttributeName.TexCoord1, [])
    slots = data.get(AttributeName.BoneIndex)
    weights = data.get(AttributeName.BoneWeight)

    out = []
    for index, submesh in enumerate(mesh.submeshes):
        triangles = _triangles(submesh.indices, submesh.primitive_mode)
        if not triangles:
            continue

        sub = ir.SubMesh(
            name="%s_%d" % (material_name or "mesh", index),
            material=material_name,
            positions=positions,
            normals=normals,
            tangents=tangents,
            colors=colors,
            uv0=uv0,
            uv1=uv1,
            triangles=triangles,
        )
        _skin(sub, submesh, slots, weights, len(positions))
        out.append(sub)
    return out


def _skin(sub, submesh, slots, weights, count) -> None:
    palette = submesh.bone_indices
    for vertex in range(count):
        bones: list = []
        values: list = []
        if slots and weights and vertex < len(slots):
            for slot, weight in zip(slots[vertex], weights[vertex]):
                index = int(slot)
                if weight <= 0.0 or index >= len(palette):
                    continue
                bones.append(palette[index])
                values.append(weight)
        elif palette:
            bones, values = [palette[0]], [1.0]

        total = sum(values)
        if total > 0:
            values = [v / total for v in values]
        sub.bones.append(bones)
        sub.weights.append(values)
