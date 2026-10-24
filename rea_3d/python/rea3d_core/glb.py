"""Small dependency-free glTF 2.0 writer for ctr intermediate objects.

SPDX-License-Identifier: GPL-3.0-or-later
Coordinates remain source Y-up. No Blender/Maya/Electron process is used.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct

from ctr.image.png import encode_rgba

from .bridge import AssetError, LIMITATIONS


def _output_path(raw):
    """Validate the supplied path before resolving links; reserve only new directories."""
    path = Path(raw)
    if not path.is_absolute() or ".." in path.parts:
        raise AssetError("INVALID_OUTPUT_PATH", "output_dir must be absolute and contain no parent traversal")
    # A resolve-first check would hide an ancestor symlink or Windows junction.
    for candidate in (path, *path.parents):
        is_junction = getattr(candidate, "is_junction", lambda: False)
        if candidate.is_symlink() or is_junction():
            raise AssetError("OUTPUT_LINK_PATH", "output_dir and its ancestors must not be symbolic links or junctions")
    if os.path.lexists(path):
        raise AssetError("OUTPUT_EXISTS", "output_dir already exists; select a new output directory")
    return path.resolve(strict=False)


def quaternion(euler):
    x, y, z = (float(v) * 0.5 for v in euler)
    sx, cx, sy, cy, sz, cz = math.sin(x), math.cos(x), math.sin(y), math.cos(y), math.sin(z), math.cos(z)
    return (sx * cy * cz - cx * sy * sz,
            cx * sy * cz + sx * cy * sz,
            cx * cy * sz - sx * sy * cz,
            cx * cy * cz + sx * sy * sz)


def _rest_half_axis_angle(euler):
    """The GFMotion axis-angle vector contains axis * half-angle, not Euler angles."""
    x, y, z, w = quaternion(euler)
    # q and -q describe the same orientation. Use the shortest principal vector.
    if w < 0:
        x, y, z, w = -x, -y, -z, -w
    vector_length = math.sqrt(x*x + y*y + z*z)
    if vector_length < 1e-12:
        return (0.0, 0.0, 0.0)
    half_angle = math.atan2(vector_length, w)
    return tuple(v * half_angle / vector_length for v in (x, y, z))


def _sample_transform(track, frame, rest):
    """Fill sparse curves in their own representation before producing a quaternion.

    Upstream sample_bone fills absent axis-angle channels from rest Euler values.
    Keep vendor code unchanged and handle sparse axis-angle curves at this boundary.
    """
    def components(curves, defaults, label):
        values = [curves[i].sample(frame) if i < len(curves) and curves[i] else defaults[i] for i in range(3)]
        return _finite(values, 3, label)

    scale = components(track.scale, rest.scale, "animated scale")
    translation = components(track.translation, rest.translation, "animated translation")
    if not any(track.rotation):
        rotation = quaternion(_finite(rest.rotation, 3, "rest rotation"))
    elif track.is_axis_angle:
        defaults = _rest_half_axis_angle(_finite(rest.rotation, 3, "rest rotation"))
        vector = components(track.rotation, defaults, "animated half-axis-angle")
        half_angle = math.sqrt(sum(v*v for v in vector))
        if half_angle < 1e-12:
            rotation = (0.0, 0.0, 0.0, 1.0)
        else:
            factor = math.sin(half_angle) / half_angle
            rotation = tuple(v * factor for v in vector) + (math.cos(half_angle),)
    else:
        rotation = quaternion(components(track.rotation, rest.rotation, "animated Euler rotation"))
    return scale, rotation, translation


def local_matrix(scale, rotation, translation):
    x, y, z, w = quaternion(rotation)
    s = scale
    return [
        [(1 - 2*y*y - 2*z*z)*s[0], (2*x*y - 2*z*w)*s[1], (2*x*z + 2*y*w)*s[2], translation[0]],
        [(2*x*y + 2*z*w)*s[0], (1 - 2*x*x - 2*z*z)*s[1], (2*y*z - 2*x*w)*s[2], translation[1]],
        [(2*x*z - 2*y*w)*s[0], (2*y*z + 2*x*w)*s[1], (1 - 2*x*x - 2*y*y)*s[2], translation[2]],
        [0.0, 0.0, 0.0, 1.0],
    ]


def multiply(a, b):
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def inverse(matrix):
    augmented = [list(row) + [float(i == j) for j in range(4)] for i, row in enumerate(matrix)]
    for column in range(4):
        pivot = max(range(column, 4), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise AssetError("INVALID_SKELETON", "rest transform is singular")
        augmented[pivot], augmented[column] = augmented[column], augmented[pivot]
        value = augmented[column][column]
        augmented[column] = [x / value for x in augmented[column]]
        for row in range(4):
            if row != column:
                factor = augmented[row][column]
                augmented[row] = [x - factor*y for x, y in zip(augmented[row], augmented[column])]
    return [row[4:] for row in augmented]


def _finite(values, count, label):
    result = tuple(float(v) for v in values[:count])
    if len(result) != count or not all(math.isfinite(v) for v in result):
        raise AssetError("INVALID_ASSET", f"{label} requires {count} finite components")
    return result


class Document:
    def __init__(self):
        self.blob = bytearray()
        self.doc = {"asset": {"version": "2.0", "generator": "REA 3D Lab pure Python exporter"},
                    "scene": 0, "scenes": [{"nodes": []}], "nodes": [],
                    "meshes": [], "accessors": [], "bufferViews": [], "buffers": []}

    def view(self, data, target=None):
        self.blob.extend(bytes((-len(self.blob)) % 4))
        index = len(self.doc["bufferViews"])
        view = {"buffer": 0, "byteOffset": len(self.blob), "byteLength": len(data)}
        if target:
            view["target"] = target
        self.doc["bufferViews"].append(view)
        self.blob.extend(data)
        return index

    def accessor(self, rows, components, component_type=5126, target=None, bounds=False):
        rows = [tuple(row) for row in rows]
        if not rows:
            raise AssetError("INVALID_ASSET", "cannot write an empty accessor")
        formats = {5126: "f", 5125: "I", 5123: "H"}
        packer = struct.Struct("<" + formats[component_type] * components)
        try:
            payload = b"".join(packer.pack(*row) for row in rows)
        except (ValueError, struct.error) as exc:
            raise AssetError("INVALID_ASSET", f"invalid accessor data: {exc}") from exc
        types = {1: "SCALAR", 2: "VEC2", 3: "VEC3", 4: "VEC4", 16: "MAT4"}
        acc = {"bufferView": self.view(payload, target), "componentType": component_type,
               "count": len(rows), "type": types[components]}
        if bounds:
            acc["min"] = [min(row[i] for row in rows) for i in range(components)]
            acc["max"] = [max(row[i] for row in rows) for i in range(components)]
        index = len(self.doc["accessors"])
        self.doc["accessors"].append(acc)
        return index

    def serialize(self):
        self.doc["buffers"] = [{"byteLength": len(self.blob)}]
        encoded = json.dumps(self.doc, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
        encoded += b" " * ((-len(encoded)) % 4)
        binary = bytes(self.blob) + bytes((-len(self.blob)) % 4)
        length = 12 + 8 + len(encoded) + 8 + len(binary)
        return (struct.pack("<III", 0x46546C67, 2, length)
                + struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
                + struct.pack("<II", len(binary), 0x004E4942) + binary)


def _skeleton(document, model):
    if len(model.bones) > 65535:
        raise AssetError("LIMIT_EXCEEDED", "skeleton exceeds 65535 joints")
    names = [b.name for b in model.bones]
    if len(set(names)) != len(names):
        raise AssetError("INVALID_SKELETON", "bone names must be unique")
    by_name = {name: i for i, name in enumerate(names)}
    world, visiting = {}, set()

    def build_world(index):
        if index in world:
            return world[index]
        if index in visiting:
            raise AssetError("INVALID_SKELETON", "bone hierarchy contains a cycle")
        visiting.add(index)
        bone = model.bones[index]
        local = local_matrix(_finite(bone.scale, 3, "bone scale"),
                             _finite(bone.rotation, 3, "bone rotation"),
                             _finite(bone.translation, 3, "bone translation"))
        if bone.parent:
            if bone.parent not in by_name:
                raise AssetError("INVALID_SKELETON", f"missing parent bone: {bone.parent}")
            local = multiply(build_world(by_name[bone.parent]), local)
        visiting.remove(index)
        world[index] = local
        return local

    for i, bone in enumerate(model.bones):
        document.doc["nodes"].append({"name": bone.name, "translation": list(bone.translation),
                                       "rotation": list(quaternion(bone.rotation)), "scale": list(bone.scale)})
        build_world(i)
    roots = []
    for i, bone in enumerate(model.bones):
        if bone.parent:
            document.doc["nodes"][by_name[bone.parent]].setdefault("children", []).append(i)
        else:
            roots.append(i)
    document.doc["scenes"][0]["nodes"].extend(roots)
    matrices = []
    for i in range(len(model.bones)):
        matrix = inverse(world[i])
        matrices.append(tuple(matrix[r][c] for c in range(4) for r in range(4)))
    if matrices:
        skin = {"joints": list(range(len(model.bones))),
                "inverseBindMatrices": document.accessor(matrices, 16)}
        if len(roots) == 1:
            skin["skeleton"] = roots[0]
        document.doc["skins"] = [skin]
    return by_name


def _materials(document, model, textures, warnings):
    images, outputs = {}, []
    names = {}
    for i, texture in enumerate(textures):
        if texture.name in names:
            prior = names[texture.name]
            if (prior.width, prior.height, prior.rgba) != (texture.width, texture.height, texture.rgba):
                raise AssetError("AMBIGUOUS_TEXTURE", f"different textures share name {texture.name}")
            continue
        names[texture.name] = texture
        png = encode_rgba(texture.rgba, texture.width, texture.height)
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", texture.name).strip(".")[:90] or "texture"
        filename = f"texture_{i:03d}_{safe}.png"
        outputs.append((filename, png))
        images[texture.name] = len(document.doc.setdefault("images", []))
        document.doc["images"].append({"name": texture.name, "bufferView": document.view(png), "mimeType": "image/png"})
    result = {}
    wrap = {0: 33071, 1: 33071, 2: 10497, 3: 33648}
    for material in model.materials:
        entry = {"name": material.name, "doubleSided": True,
                 "pbrMetallicRoughness": {"baseColorFactor": [max(0.0, min(1.0, v)) for v in _finite(material.diffuse, 4, "diffuse")],
                                          "metallicFactor": 0, "roughnessFactor": 1},
                 "extensions": {"KHR_materials_unlit": {}}}
        document.doc.setdefault("extensionsUsed", ["KHR_materials_unlit"])
        if material.textures:
            slot = material.textures[0]
            if slot.name in images:
                sampler_id = len(document.doc.setdefault("samplers", []))
                document.doc["samplers"].append({"wrapS": wrap.get(slot.wrap_u, 10497), "wrapT": wrap.get(slot.wrap_v, 10497)})
                texture_id = len(document.doc.setdefault("textures", []))
                document.doc["textures"].append({"source": images[slot.name], "sampler": sampler_id})
                info = {"index": texture_id, "texCoord": 0}
                if slot.scale != (1.0, 1.0) or slot.rotation != 0 or slot.translation != (0.0, 0.0):
                    if "KHR_texture_transform" not in document.doc["extensionsUsed"]:
                        document.doc["extensionsUsed"].append("KHR_texture_transform")
                    info["extensions"] = {"KHR_texture_transform": {"offset": list(slot.translation),
                                                                      "rotation": slot.rotation, "scale": list(slot.scale)}}
                entry["pbrMetallicRoughness"]["baseColorTexture"] = info
                if any(names[slot.name].rgba[n] < 255 for n in range(3, len(names[slot.name].rgba), 4)):
                    entry["alphaMode"] = "BLEND"
                if slot.wrap_u == 1 or slot.wrap_v == 1:
                    warnings.append({"code": "MATERIAL_WRAP_APPROXIMATION", "material": material.name,
                                     "message": "Clamp-to-border was approximated with clamp-to-edge."})
            else:
                warnings.append({"code": "MISSING_TEXTURE", "material": material.name,
                                 "message": f"Base colour texture not supplied: {slot.name}"})
            if len(material.textures) > 1:
                warnings.append({"code": "STANDARD_GLTF_MATERIAL_FALLBACK" if getattr(model, "pica_config", None) else "MATERIAL_TEXTURE_UNITS_OMITTED", "material": material.name,
                                 "message": "Standard glTF maps the base texture; the REA PICA runtime uses all decoded texture units when available."})
        if material.diffuse[3] < 1:
            entry["alphaMode"] = "BLEND"
        result[material.name] = len(document.doc.setdefault("materials", []))
        document.doc["materials"].append(entry)
    return result, outputs


def _meshes(document, model, materials, warnings):
    vertices = triangles = 0
    for sub in model.submeshes:
        if not sub.positions or not sub.triangles:
            warnings.append({"code": "EMPTY_MESH_OMITTED", "asset": sub.name, "message": "Mesh has no triangles or positions."})
            continue
        count = len(sub.positions)
        positions = [_finite(p, 3, "position") for p in sub.positions]
        attributes = {"POSITION": document.accessor(positions, 3, target=34962, bounds=True)}
        for key, values, width in [("NORMAL", sub.normals, 3), ("TEXCOORD_0", sub.uv0, 2),
                                   ("TEXCOORD_1", sub.uv1, 2), ("TEXCOORD_2", sub.uv2, 2), ("COLOR_0", sub.colors, 4)]:
            if values:
                if len(values) != count:
                    raise AssetError("INVALID_MESH", f"{sub.name}: {key} count does not match vertex count")
                rows = [_finite(v, width, key) for v in values]
                if key.startswith("TEXCOORD_") and getattr(sub, "bch_flip_v", False):
                    rows = [(u, 1.0-v) for u, v in rows]
                if key == "NORMAL":
                    rows = [tuple(v / (math.sqrt(sum(x*x for x in row)) or 1) for v in row) for row in rows]
                elif key == "COLOR_0":
                    rows = [tuple(max(0.0, min(1.0, v)) for v in row) for row in rows]
                attributes[key] = document.accessor(rows, width, target=34962)
        if sub.tangents:
            if getattr(sub, "bch_flip_v", False):
                attributes["_PICA_TANGENT"] = document.accessor([_finite(v, 3, "PICA tangent") for v in sub.tangents], 3, target=34962)
            warnings.append({"code": "TANGENT_SIGN_UNAVAILABLE", "asset": sub.name,
                             "message": "Source tangents lack glTF handedness and were omitted."})
        skinned = bool(model.bones and sub.bones and any(sub.weights))
        if skinned:
            if len(sub.bones) != count or len(sub.weights) != count:
                raise AssetError("INVALID_SKIN", "skin row count does not match vertices")
            influences = []
            for bones, weights in zip(sub.bones, sub.weights):
                if len(bones) != len(weights) or not weights or sum(weights) <= 0:
                    raise AssetError("INVALID_SKIN", "each skinned vertex needs matching nonzero influences")
                if any(not isinstance(b, int) or b < 0 or b >= len(model.bones) for b in bones):
                    raise AssetError("INVALID_SKIN", "bone influence lies outside the skeleton")
                if any(not math.isfinite(w) or w < 0 for w in weights):
                    raise AssetError("INVALID_SKIN", "weights must be finite and nonnegative")
                total = sum(weights)
                influences.append((list(bones), [w / total for w in weights]))
            groups = (max(len(b) for b, _ in influences) + 3) // 4
            for group in range(groups):
                offset = group * 4
                joints = [tuple((b[offset:offset+4] + [0]*4)[:4]) for b, _ in influences]
                weights = [tuple((w[offset:offset+4] + [0.0]*4)[:4]) for _, w in influences]
                attributes[f"JOINTS_{group}"] = document.accessor(joints, 4, 5123, 34962)
                attributes[f"WEIGHTS_{group}"] = document.accessor(weights, 4, 5126, 34962)
        elif model.bones:
            warnings.append({"code": "UNSKINNED_MESH", "asset": sub.name, "message": "No positive skin influences were available for this mesh."})
        indices = []
        for triangle in sub.triangles:
            if len(triangle) != 3 or any(not isinstance(i, int) or i < 0 or i >= count for i in triangle):
                raise AssetError("INVALID_MESH", "triangle index lies outside the vertex buffer")
            indices.extend((i,) for i in triangle)
        primitive = {"attributes": attributes, "indices": document.accessor(indices, 1, 5125, 34963), "mode": 4}
        if sub.material in materials:
            primitive["material"] = materials[sub.material]
        elif sub.material:
            warnings.append({"code": "MISSING_MATERIAL", "asset": sub.name,
                             "message": f"Referenced material was not parsed: {sub.material}"})
        mesh_index = len(document.doc["meshes"])
        document.doc["meshes"].append({"name": sub.name, "primitives": [primitive]})
        node = {"name": sub.name, "mesh": mesh_index}
        if getattr(sub, "pica_node_name", None):
            node.setdefault("extras", {}).update({"reaPicaNodeName": sub.pica_node_name,
                                                 "reaPicaDefaultVisible": sub.pica_default_visible})
        if getattr(sub, "bch_flip_v", False):
            node.setdefault("extras", {})["uvConversion"] = "BCH source V -> 1-V; PNG pixels unchanged"
        if skinned:
            node["skin"] = 0
        document.doc["scenes"][0]["nodes"].append(len(document.doc["nodes"]))
        document.doc["nodes"].append(node)
        vertices += count
        triangles += len(sub.triangles)
    if not document.doc["meshes"]:
        raise AssetError("NO_GEOMETRY", "no drawable mesh was parsed from the selected model")
    return vertices, triangles


def _animations(document, model, motions, bones, fps, warnings):
    stats = []
    rest = {b.name: b for b in model.bones}
    for label, motion in motions:
        tracks = [b for b in motion.bones if b.name in bones and b.animated and not getattr(b, "gf1_world_space", False)]
        omitted = [b.name for b in motion.bones if b.name not in bones]
        if omitted:
            warnings.append({"code": "UNBOUND_ANIMATION_TRACKS", "asset": label, "bones": omitted,
                             "message": "Some tracks did not match the selected skeleton and were omitted."})
        if not tracks:
            raise AssetError("NO_TRACKS_BOUND", f"motion did not bind any animated tracks: {label}")
        frame_count = max(1, motion.frame_count)
        if frame_count > 10000 or frame_count * len(tracks) > 1000000:
            raise AssetError("LIMIT_EXCEEDED", "animation sample budget exceeded")
        if len({t.name for t in tracks}) != len(tracks):
            raise AssetError("INVALID_ANIMATION", "duplicate animation bone tracks")
        times = document.accessor([(f / fps,) for f in range(frame_count)], 1, bounds=True)
        animation = {"name": label, "samplers": [], "channels": [],
                     "extras": {"sourceFrames": motion.frame_count, "fps": fps, "looping": motion.looping,
                                "sourceFrameRate": fps, "frameRateMeaning": "source_frame_rate_interpretation",
                                "timestampFormula": "source_frame/fps", "resampled": False,
                                "interpolationNote": "Evaluated once per integer source frame; fps interprets source timing (default 30), not output resampling. Subframe fidelity is not established."}}
        for track in tracks:
            samples = [_sample_transform(track, float(f), rest[track.name]) for f in range(frame_count)]
            scales = [_finite(s, 3, "animated scale") for s, r, t in samples]
            translations = [_finite(t, 3, "animated translation") for s, r, t in samples]
            rotations = [_finite(r, 4, "animated quaternion") for s, r, t in samples]
            for i in range(1, len(rotations)):
                if sum(a*b for a, b in zip(rotations[i-1], rotations[i])) < 0:
                    rotations[i] = tuple(-v for v in rotations[i])
            for path, rows, width in [("scale", scales, 3), ("rotation", rotations, 4), ("translation", translations, 3)]:
                sampler_id = len(animation["samplers"])
                animation["samplers"].append({"input": times, "output": document.accessor(rows, width), "interpolation": "LINEAR"})
                animation["channels"].append({"sampler": sampler_id, "target": {"node": bones[track.name], "path": path}})
        document.doc.setdefault("animations", []).append(animation)
        stats.append({"name": label, "frames": frame_count, "bound_bones": len(tracks), "unbound_bones": omitted})
    return stats


def export_glb(assets, args):
    index = args.get("model_index", 0)
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(assets.models):
        raise AssetError("NO_MODEL", "model_index does not select a parsed model")
    fps = args.get("fps", 30)
    if isinstance(fps, bool) or not isinstance(fps, (int, float)) or not math.isfinite(fps) or not 1 <= fps <= 240:
        raise AssetError("INVALID_ARGUMENT", "fps must be a finite number between 1 and 240")
    if not isinstance(args.get("output_dir"), str):
        raise AssetError("INVALID_ARGUMENT", "output_dir must be a path string")
    output = _output_path(args["output_dir"])
    model, document = assets.models[index], Document()
    warnings = list(assets.warnings)
    if len(assets.models) > 1:
        warnings.append({"code": "MODEL_SELECTION", "message": f"Exporting model {index}; {len(assets.models)-1} other model(s) are not in this GLB."})
    bones = _skeleton(document, model)
    materials, images = _materials(document, model, assets.textures, warnings)
    vertices, triangles = _meshes(document, model, materials, warnings)
    animations = _animations(document, model, assets.motions, bones, fps, warnings)
    from .runtime_export import make_runtime
    pica_runtime = make_runtime(document, model, assets, animations, fps)
    document.doc["extras"] = {"limitations": LIMITATIONS, "sources": assets.sources,
                               "sourceCoordinateSystem": "Y-up, source units", "realGameVerified": False,
                               "uvConversion": {"BCH": "V -> 1-V for TEXCOORD_0/1/2; decoded PNG pixels unchanged",
                                                "GFModel": "source UV unchanged"},
                               "animationTiming": {"sourceFrameRate": fps, "defaultSourceFrameRate": 30,
                                                   "timestampFormula": "source_frame/fps", "resampled": False}}
    if pica_runtime:
        document.doc["extras"]["reaPicaRuntime"] = pica_runtime
    payload = document.serialize()
    # Repeat after conversion, then use an exclusive mkdir to close a direct-directory
    # creation race with Node's earlier check or another producer.
    output = _output_path(args["output_dir"])
    try:
        output.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise AssetError("OUTPUT_EXISTS", "output_dir was created before it could be reserved") from exc
    with (output / "model.glb").open("xb") as handle:
        handle.write(payload)
    for filename, png in images:
        with (output / filename).open("xb") as handle:
            handle.write(png)
    if pica_runtime:
        with (output / "pica-runtime.json").open("x", encoding="utf-8") as handle:
            json.dump(pica_runtime, handle, ensure_ascii=False, allow_nan=False, indent=2)
    anim_partial = any(w["code"] in {"UNSUPPORTED_ANIMATION_CHANNELS", "NO_SKELETAL_TRACKS", "UNBOUND_ANIMATION_TRACKS",
                                      "BCH_REAL_DATA_UNVERIFIED", "UNIDENTIFIED_ASSET", "PARSE_FAILED"}
                       or w["code"].startswith("GF1_") for w in warnings)
    result = {"status": "partial" if warnings else "converted_supported_data",
              "glb_path": str(output / "model.glb"), "receipt_path": str(output / "receipt.json"),
              "png_paths": [str(output / name) for name, _ in images],
              "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload),
              "selected_model": index, "mesh_count": len(document.doc["meshes"]), "vertices": vertices,
              "triangles": triangles, "bone_count": len(model.bones), "texture_count": len(images),
              "animation_count": len(animations), "requested_motion_file_count": len(args.get("motion_paths", [])),
              "animation_export_complete": not anim_partial,
              "animations": animations, "warnings": warnings, "sources": assets.sources,
              "animation_timing": {"source_frame_rate": fps, "default_source_frame_rate": 30,
                                   "timestamps": "source_frame/fps", "resampling": False},
              "limitations": LIMITATIONS, "real_game_asset_verified": False,
              "original_rendering_verified": False}
    if pica_runtime:
        result["pica_runtime_path"] = str(output / "pica-runtime.json")
        result["pica_material_count"] = len(pica_runtime["materials"])
        result["material_animation_clip_count"] = sum(bool(clip["material_animations"]) for clip in pica_runtime["clips"])
        result["visibility_animation_clip_count"] = sum(bool(clip["visibility_animations"]) for clip in pica_runtime["clips"])
        result["auxiliary_pose_clip_count"] = sum(clip.get("role") == "auxiliary_pose" for clip in pica_runtime["clips"])
    with (output / "receipt.json").open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")
    return result
