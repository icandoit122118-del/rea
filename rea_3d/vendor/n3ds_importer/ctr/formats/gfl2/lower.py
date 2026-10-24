from __future__ import annotations

from ... import ir
from ...pica.attributes import AttributeName
from .gfmodel import Model as RawModel

UNUSED_BONE = 0xFF

TRIANGLES = 0
TRIANGLE_STRIP = 3

def _strip_to_triangles(indices: list) -> list:
    tris = []
    for i in range(len(indices) - 2):
        a, b, c = indices[i], indices[i + 1], indices[i + 2]
        if a == b or b == c or a == c:
            continue
        tris.append((a, c, b) if i & 1 else (a, b, c))
    return tris

def _triangles(indices: list, primitive_mode: int) -> list:
    if primitive_mode == TRIANGLE_STRIP:
        return _strip_to_triangles(indices)
    return [
        tuple(indices[i:i + 3])
        for i in range(0, len(indices) - 2, 3)
        if len(set(indices[i:i + 3])) == 3
    ]

def lower_model(raw: RawModel) -> ir.Model:
    model = ir.Model(
        name=raw.name,
        texture_names=[h.name for h in raw.texture_names],
        material_names=[h.name for h in raw.material_names],
    )

    for material in raw.materials:
        model.materials.append(ir.Material(
            name=material.name,
            diffuse=material.diffuse,
            ambient=material.ambient,
            emission=material.emission,
            textures=[
                ir.TextureSlot(
                    name=coord.name,
                    unit=coord.unit,
                    scale=coord.scale,
                    rotation=coord.rotation,
                    translation=coord.translation,
                    wrap_u=coord.wrap_u,
                    wrap_v=coord.wrap_v,
                )
                for coord in material.coords if coord.name
            ],
        ))

    for bone in raw.bones:
        model.bones.append(ir.Bone(
            name=bone.name,
            parent=bone.parent or None,
            translation=bone.translation,
            rotation=bone.rotation,
            scale=bone.scale,
        ))

    for mesh_index, mesh in enumerate(raw.meshes):
        material = (
            raw.material_names[mesh_index].name
            if mesh_index < len(raw.material_names) else ""
        )
        for raw_sub in mesh.submeshes:
            model.submeshes.append(_lower_submesh(raw_sub, material))

    return model

def _lower_submesh(raw_sub, material: str) -> ir.SubMesh:
    data = raw_sub.vertices()
    fixed = {f.name: f.value for f in raw_sub.fixed_attributes}
    count = raw_sub.vertex_count

    sub = ir.SubMesh(name=raw_sub.name, material=material)
    sub.positions = data.get(AttributeName.Position, [])
    sub.normals = data.get(AttributeName.Normal, [])
    sub.tangents = data.get(AttributeName.Tangent, [])

    for slot, name in (
        ("uv0", AttributeName.TexCoord0),
        ("uv1", AttributeName.TexCoord1),
        ("uv2", AttributeName.TexCoord2),
    ):
        if name in data:
            setattr(sub, slot, data[name])
        elif name in fixed:
            setattr(sub, slot, [fixed[name][:2]] * count)

    if AttributeName.Color in data:
        sub.colors = data[AttributeName.Color]
    elif AttributeName.Color in fixed:
        sub.colors = [fixed[AttributeName.Color]] * count

    palette = raw_sub.bone_indices
    raw_indices = data.get(AttributeName.BoneIndex)
    raw_weights = data.get(AttributeName.BoneWeight)

    for v in range(count):
        bones: list = []
        weights: list = []
        if raw_indices and raw_weights:
            for slot_index, slot_weight in zip(raw_indices[v], raw_weights[v]):
                if slot_index == UNUSED_BONE or slot_weight <= 0.0:
                    continue
                if slot_index >= len(palette):
                    continue
                bones.append(palette[slot_index])
                weights.append(slot_weight)
        elif palette:
            bones, weights = [palette[0]], [1.0]

        total = sum(weights)
        if total > 0:
            weights = [w / total for w in weights]
        sub.bones.append(bones)
        sub.weights.append(weights)

    sub.triangles = _triangles(raw_sub.indices, raw_sub.primitive_mode)
    return sub
