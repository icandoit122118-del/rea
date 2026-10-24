"""Pure Python adapter around the pinned upstream readers.

SPDX-License-Identifier: GPL-3.0-or-later
This adapter is new; vendored ctr readers remain unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import math
from pathlib import Path
import struct

from ctr.binstream import BinStream
from ctr.compress.lz import decompress_lz, sniff_lz
from ctr.container.pc_ps import read_pc_ps, sniff_pc_ps
from ctr.formats.bch.header import sniff_bch, read_header, relocate
from ctr.formats.bch.contents import read_contents
from ctr.formats.bch.lower import _lower_model, _lower_mesh, lower_bch_animations, lower_bch_textures
from ctr.formats.bch.model import read_models
from ctr.formats.bch.mesh import read_meshes
from ctr.formats.bch.material import Material as BCHMaterial
from ctr.formats.bch.tree import read_cstring
from ctr.formats.gfl2.gfmodel import read_gfmodel, sniff_gfmodel
from ctr.formats.gfl2.gftexture import read_gftexture, sniff_gftexture
from ctr.formats.gfl2.gfmotion import read_motion
from ctr.formats.gfl2.lower import lower_model
from ctr.ir import Texture
from ctr.pica import commands as pica
from ctr.pica.attributes import AttributeName, FixedAttribute

MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_LEAVES = 4096

LIMITATIONS = [
    "No ROM decryption or copyrighted game data is supplied.",
    "GARC/DARC/SARC and CGFX require a separate extraction/parser step.",
    "BCH material/visibility animation and decoded PICA material states are embedded for the REA PICA player; standard glTF viewers show a skeletal/unlit fallback.",
    "The REA player implements source UV transforms, texture stages, alpha tests, normal mapping and reflection LUTs. Original-game lighting/camera, hardware precision and visual parity require separate reference verification.",
    "PNG base levels are preserved; GPU-generated mipmaps and controlled preview lighting are distinguished from original-game state. Effects and camera animation are not exported.",
    "Selected-file parsing/render checks do not establish all BCH variants or original-game parity; see the external provenance/verification receipt.",
    "The fps option interprets the source frame rate (default 30); timestamps are source_frame/fps. Motion is evaluated once per integer source frame, not resampled to a new frame rate. Subframe fidelity and original in-game appearance are unverified.",
    "ORAS GF1 motion loop/header flag semantics are unverified and world-space tracks are explicitly omitted. Original opcode6 smoothstep and opcode7 slope units are verified only for identified ORAS payloads listed in source reports; other profiles retain reference assumptions. Native float32 precision, subframe reconstruction and original-game visual parity remain separate verification boundaries.",
]


class AssetError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


@dataclass
class Assets:
    sources: list = field(default_factory=list)
    nodes: list = field(default_factory=list)
    models: list = field(default_factory=list)
    textures: list = field(default_factory=list)
    motions: list = field(default_factory=list)
    visual_motion_packs: list = field(default_factory=list)
    facial_motion_packages: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    expanded: int = 0


def capabilities():
    return {
        "implementation": "pinned_n3ds_ctr_plus_pure_python_glb_writer",
        "upstream_commit": "a68959eae2f476526845a78af6990648cfe3434d",
        "license": "GPL-3.0-or-later",
        "gui_required": False,
        "external_python_dependencies": [],
        "methods": ["capabilities", "inspect", "export"],
        "containers": ["PC", "PS", "PB", "PK", "PT", "PF"],
        "compression": ["LZ10", "LZ11"],
        "models": ["GFModel", "BCH"],
        "textures": ["GFTexture", "BCH textures"],
        "animations": ["GFMotion skeletal", "BCH skeletal", "GF1MotionPack local skeletal (PB/PK entry 0)", "BCH material UV/RGBA/texture selection", "BCH mesh/model visibility", "PF independent facial material/visibility (identified ORAS Bulbasaur profile only)"],
        "pica_runtime": {"schema": "rea.pica.runtime.v1", "required_player": "REA PICA player", "standard_gltf_material_fallback": True},
        "pf_facial": {"schema": "rea3d-pf-facial-runtime-v1", "input_option": "motion_paths",
                      "maximum_packages": 1, "supported_model": "pm0001_00",
                      "profile_gate": "decompressed PF SHA-256 identity",
                      "adds_standard_gltf_animation_clips": False,
                      "automatic_blink_wall_clock_verified": False,
                      "original_game_parity_verified": False},
        "gf1_reference": "gdkchan/SPICA@bd29a7828595d7839cda2ac61c76bb63f9071250 (Unlicense)",
        "animation_timing": {"option": "fps", "meaning": "source_frame_rate_interpretation",
                             "default": 30, "timestamps": "source_frame/fps", "resampling": False,
                             "samples": "one sample per integer source frame"},
        "export_formats": ["GLB 2.0 with embedded PNG", "PNG", "JSON receipt"],
        "limits": {"max_file_bytes": MAX_FILE_BYTES, "max_expanded_bytes": MAX_EXPANDED_BYTES,
                   "max_depth": 8, "max_leaves": MAX_LEAVES},
        "limitations": LIMITATIONS,
    }


def _motion_sections(blob):
    """Require a bounded section table before invoking the permissive upstream parser."""
    if len(blob) < 20:
        return None
    count = struct.unpack_from("<I", blob, 4)[0]
    if not 1 <= count <= 64 or 8 + count * 12 > len(blob):
        return None
    sections = [struct.unpack_from("<III", blob, 8 + i * 12) for i in range(count)]
    if sections[0][0] != 0 or sections[0][1] < 36:
        return None
    for kind, length, offset in sections:
        if kind > 32 or offset < 8 + 12 * count or offset + length > len(blob):
            return None
    frames = struct.unpack_from("<I", blob, sections[0][2])[0]
    if frames > 100000:
        raise AssetError("LIMIT_EXCEEDED", "motion exceeds 100000 frames")
    return sections


def _package_entries(blob):
    count = struct.unpack_from("<H", blob, 2)[0]
    header_end = 4+4*(count+1)
    if count > MAX_LEAVES or header_end > len(blob):
        raise AssetError("INVALID_PACKAGE", "package entry count/offset table exceeds its bounds")
    offsets = struct.unpack_from("<"+"I"*(count+1), blob, 4)
    if offsets[0] < header_end or offsets[-1] > len(blob) or any(a > b for a, b in zip(offsets, offsets[1:])):
        raise AssetError("INVALID_PACKAGE", "package offsets overlap the header, descend, or exceed the file")
    return [blob[a:b] for a, b in zip(offsets, offsets[1:])]


def _bch_materials_v21(data, at, count):
    """SPICA H3DMaterial >=0x21 uses a mapper pointer instead of 3 inline mappers.

    The pinned ctr reader implements only the older 0x58-byte material layout.
    Version 0x21's material is 0x2c bytes, with texture names at 0x1c..0x24.
    """
    if count > 4096 or at < 0 or at+count*0x2c > len(data):
        raise ValueError("BCH v0x21 material table exceeds its section bounds")
    materials = []
    for index in range(count):
        offset = at+index*0x2c
        pointers = struct.unpack_from("<4I", data, offset+0x1c)
        names = [read_cstring(data, pointer) for pointer in pointers]
        if any(any(ord(ch) < 32 for ch in name) for name in names):
            raise ValueError("BCH material contains control characters in a name")
        materials.append(BCHMaterial(name=names[3], textures=[name for name in names[:3] if name]))
    return materials


def _float24_vector(words):
    """PICA fixed vectors have 4 packed float24 values in X/Y/Z/W order."""
    first, second, third = words
    values = [third & 0xffffff, (third >> 24) | ((second & 0xffff) << 8),
              (second >> 16) | ((first & 0xff) << 16), first >> 8]
    output = []
    for value in values:
        bits = (((value & 0xffff) << 7) | ((((value >> 16) & 0x7f)+64) << 23)
                | ((value >> 23) << 31)) if value & 0x7fffff else ((value & 0x800000) << 8)
        output.append(struct.unpack("<f", struct.pack("<I", bits))[0])
    return tuple(output)


def _bch_command_state(words):
    uniforms, fixed_words = {}, {}
    uniform_index, uniform32, fixed_index = 0, False, 0
    pending24 = [0, 0, 0]
    formats, permutation, total = 0, 0, 0
    for command in pica.decode_commands(words):
        register, value = command.register, command.param
        if register == 0x2c0:
            uniform_index, uniform32 = (value & 0xff)*4, bool(value >> 31)
        elif 0x2c1 <= register <= 0x2c8:
            for value in command.parameters:
                index = (uniform_index >> 2) & 0x5f
                target = uniforms.setdefault(index, [0., 0., 0., 0.])
                slot = uniform_index & 3
                if uniform32:
                    target[3-slot] = struct.unpack("<f", struct.pack("<I", value))[0]
                else:
                    pending24[slot] = value
                    if slot == 2:
                        uniforms[index] = list(_float24_vector(pending24))
                        uniform_index += 1
                uniform_index += 1
        elif register == pica.FIXEDATTRIB_INDEX:
            fixed_index = value
        elif pica.FIXEDATTRIB_DATA0 <= register <= pica.FIXEDATTRIB_DATA2:
            fixed_words.setdefault(fixed_index, [0, 0, 0])[register-pica.FIXEDATTRIB_DATA0] = value
        elif register == pica.ATTRIBBUFFERS_FORMAT_LOW:
            formats |= value
        elif register == pica.ATTRIBBUFFERS_FORMAT_HIGH:
            formats |= value << 32
        elif register == pica.VSH_ATTRIBUTES_PERMUTATION_LOW:
            permutation |= value
        elif register == pica.VSH_ATTRIBUTES_PERMUTATION_HIGH:
            permutation |= value << 32
        elif register == pica.VSH_NUM_ATTR:
            total = value+1
    fixed = {}
    for index in range(min(total, 16)):
        if (formats >> (48+index)) & 1:
            name = AttributeName((permutation >> (4*index)) & 15)
            fixed[name] = _float24_vector(fixed_words.get(index, [0, 0, 0]))
    if any(not math.isfinite(value) for values in uniforms.values() for value in values):
        raise ValueError("PICA uniform contains a non-finite number")
    return uniforms, fixed


def _adapt_bch_geometry(data, raw, model):
    """Apply shipped PICA scales/fixed attributes and source rigid bind transforms.

    Reference: SPICA H3DMesh.Deserialize, VerticesConverter.GetVertices,
    MeshTransform.GetWorldSpaceVertices at the pinned SPICA revision.
    """
    from .glb import local_matrix, multiply
    world, visiting = {}, set()
    def bone_world(index):
        if index in world:
            return world[index]
        if index in visiting or not 0 <= index < len(raw.bones):
            raise ValueError("invalid BCH rigid bone hierarchy")
        visiting.add(index)
        bone = raw.bones[index]
        matrix = local_matrix(bone.scale, bone.rotation, bone.translation)
        if bone.parent >= 0:
            matrix = multiply(bone_world(bone.parent), matrix)
        visiting.remove(index)
        world[index] = matrix
        return matrix
    scales = {AttributeName.Position: (7, 0), AttributeName.Normal: (7, 1),
              AttributeName.Tangent: (7, 2), AttributeName.Color: (7, 3),
              AttributeName.TexCoord0: (8, 0), AttributeName.TexCoord1: (8, 1),
              AttributeName.TexCoord2: (8, 2), AttributeName.BoneWeight: (8, 3)}
    model.submeshes = []
    for mesh_index, mesh in enumerate(read_meshes(data, raw)):
        mesh_at = raw.meshes_at+mesh_index*56
        if data[mesh_at+2] & 3 == 1:  # H3DMeshType.Silhouette is not regular geometry.
            continue
        commands_at, commands_count = struct.unpack_from("<II", data, mesh_at+8)
        words = list(struct.unpack_from("<%dI" % commands_count, data, commands_at))
        uniforms, fixed = _bch_command_state(words)
        mesh.attributes = [replace(attr, scale=uniforms[scales[attr.name][0]][scales[attr.name][1]])
                           if attr.name in scales and scales[attr.name][0] in uniforms else attr
                           for attr in mesh.attributes]
        values = mesh.vertices()
        for name, vector in fixed.items():
            if name not in values:
                values[name] = [tuple(int(v) for v in vector[:3]) if name == AttributeName.BoneIndex
                                else vector[:3] if name == AttributeName.BoneWeight else vector]*mesh.vertex_count
        # _lower_mesh obtains vertex arrays through mesh.vertices(). Preserve the exact
        # decoded fixed channels without monkey-patching the vendored class.
        material = raw.materials[mesh.material_index].name if mesh.material_index < len(raw.materials) else ""
        lowered = _lower_mesh(mesh, material)
        submesh_at, _ = struct.unpack_from("<II", data, mesh_at+16)
        output_index = 0
        for sub_index, raw_sub in enumerate(mesh.submeshes):
            if not raw_sub.indices or not any(len(set(raw_sub.indices[i:i+3])) == 3 for i in range(0, len(raw_sub.indices)-2, 3)):
                continue
            sub = lowered[output_index]
            output_index += 1
            sub.name = f"{material}_{mesh_index}_{sub_index}"
            sub.pica_mesh_index = mesh_index
            # The selected ORAS BCH input was checked against unchanged PNGs in
            # independent A/B renders. Keep this format-scoped, never global.
            sub.bch_flip_v = True
            count = len(sub.positions)
            offset = uniforms.get(6, (0., 0., 0., 0.))
            sub.positions = [tuple(p[i]+offset[i] for i in range(3)) for p in values.get(AttributeName.Position, [])]
            for name, field_name in [(AttributeName.Normal, "normals"), (AttributeName.Tangent, "tangents"),
                                     (AttributeName.Color, "colors"), (AttributeName.TexCoord0, "uv0"),
                                     (AttributeName.TexCoord1, "uv1"), (AttributeName.TexCoord2, "uv2")]:
                setattr(sub, field_name, list(values.get(name, [])))
            slots, weights = values.get(AttributeName.BoneIndex), values.get(AttributeName.BoneWeight)
            skinning = data[submesh_at+52*sub_index]
            if skinning not in (0, 1, 2):
                raise ValueError("unknown BCH submesh skinning mode")
            rigid = ((data[mesh_at+2] >> 2) & 3) != 2 and skinning != 1
            sub.bones, sub.weights = [], []
            for vertex in range(count):
                slot = int(slots[vertex][0]) if slots else 0
                if raw_sub.bone_indices and not 0 <= slot < len(raw_sub.bone_indices):
                    raise ValueError("BCH bone slot lies outside submesh palette")
                if rigid and raw_sub.bone_indices:
                    joint = raw_sub.bone_indices[slot]
                    matrix = bone_world(joint)
                    p = sub.positions[vertex]
                    sub.positions[vertex] = tuple(sum(matrix[row][column]*p[column] for column in range(3))+matrix[row][3] for row in range(3))
                    if sub.normals:
                        normal = sub.normals[vertex]
                        sub.normals[vertex] = tuple(sum(matrix[row][column]*normal[column] for column in range(3)) for row in range(3))
                    sub.bones.append([joint]); sub.weights.append([1.])
                elif raw_sub.bone_indices:
                    if weights:
                        vertex_slots = slots[vertex] if slots else [0]*len(weights[vertex])
                        pairs = [(raw_sub.bone_indices[int(s)], w) for s, w in zip(vertex_slots, weights[vertex])
                                 if w > 0 and 0 <= int(s) < len(raw_sub.bone_indices)]
                    else:
                        pairs = [(raw_sub.bone_indices[slot], 1.)]
                    total = sum(weight for _, weight in pairs)
                    sub.bones.append([joint for joint, _ in pairs])
                    sub.weights.append([weight/total for _, weight in pairs] if total else [])
                else:
                    sub.bones.append([]); sub.weights.append([])
            model.submeshes.append(sub)


def _lower_bch_versioned(blob):
    header = read_header(BinStream(blob))
    data = relocate(blob, header)
    contents = read_contents(data, header)
    raw_models = read_models(data, contents)
    if header.backward >= 0x21:
        pointers = [struct.unpack_from("<I", data, contents.models.values+4*i)[0]
                    for i in range(contents.models.count)]
        pointers = [pointer for pointer in pointers if pointer]
        if len(pointers) != len(raw_models):
            raise ValueError("BCH model pointer count does not match decoded models")
        for raw, pointer in zip(raw_models, pointers):
            materials_at, count, _tree = struct.unpack_from("<III", data, pointer+0x34)
            raw.materials = _bch_materials_v21(data, materials_at, count) if count else []
    models = []
    material_report = None
    if raw_models and header.backward >= 0x21:
        try:
            from .pica_material import inspect_bch_materials
            material_report = inspect_bch_materials(blob)
        except Exception as exc:
            material_report = {"models": [], "errors": [f"{type(exc).__name__}: {exc}"]}
    for raw in raw_models:
        model = _lower_model(data, raw)
        _adapt_bch_geometry(data, raw, model)
        if material_report:
            model.pica_errors = material_report.get("errors", [])
            matches = [item for item in material_report.get("models", []) if item.get("name") == model.name]
            if len(matches) == 1:
                model.pica_config = matches[0]
                model.pica_luts = material_report.get("luts", [])
                visibility = {item["mesh_index"]: item for item in matches[0].get("mesh_visibility", {}).get("meshes", [])}
                for sub in model.submeshes:
                    node = visibility.get(getattr(sub, "pica_mesh_index", -1))
                    if node:
                        sub.pica_node_name = node["node_name"]
                        sub.pica_default_visible = node.get("default_visible", True)
        models.append(model)
    return models, header, contents


def _leaves(blob, path, assets, depth=0, hint=None):
    if depth > 8:
        raise AssetError("LIMIT_EXCEEDED", "container/compression nesting exceeds 8")
    if sniff_lz(blob[:16]):
        size = int.from_bytes(blob[1:4], "little")
        if size == 0 and len(blob) >= 8:
            size = int.from_bytes(blob[4:8], "little")
        if size > MAX_EXPANDED_BYTES or assets.expanded + size > MAX_EXPANDED_BYTES:
            raise AssetError("LIMIT_EXCEEDED", "decompressed data exceeds the byte budget")
        decoded = decompress_lz(blob)
        assets.expanded += len(decoded)
        yield from _leaves(decoded, path + "/LZ", assets, depth + 1, hint)
    elif hint == "GF1MotionPack":
        if len(assets.nodes) >= MAX_LEAVES:
            raise AssetError("LIMIT_EXCEEDED", "asset leaf count exceeds 4096")
        yield path, blob, hint
    elif blob[:2] == b"PF":
        # PF ranges form independently parameterized facial controller groups.
        # Do not flatten them into ordinary BCH clips or invent a skeletal clip.
        if len(assets.nodes) >= MAX_LEAVES:
            raise AssetError("LIMIT_EXCEEDED", "asset leaf count exceeds 4096")
        yield path, blob, "PF"
    elif sniff_pc_ps(blob[:16]):
        for index, entry in enumerate(read_pc_ps(BinStream(blob))):
            if entry:
                yield from _leaves(entry, path + f"[{index}]", assets, depth + 1)
    elif blob[:2] in {b"PB", b"PK", b"PT"}:
        if blob[:2] in {b"PB", b"PK"}:
            from .bch_animation import read_pokemon_animation_package
            try:
                assets.visual_motion_packs.append(read_pokemon_animation_package(blob, label=path))
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                assets.visual_motion_packs.append({"kind": blob[:2].decode(), "label": path, "mappings": [], "errors": [message], "warnings": []})
                assets.warnings.append({"code": "VISUAL_PACKAGE_PARSE_FAILED", "asset": path, "message": message})
        for index, entry in enumerate(_package_entries(blob)):
            if entry:
                entry_hint = "GF1MotionPack" if blob[:2] in {b"PB", b"PK"} and index == 0 else None
                yield from _leaves(entry, path + f"/{blob[:2].decode()}[{index}]", assets, depth+1, entry_hint)
    else:
        if len(assets.nodes) >= MAX_LEAVES:
            raise AssetError("LIMIT_EXCEEDED", "asset leaf count exceeds 4096")
        yield path, blob, None


def read_assets(path, assets=None, required_role=None):
    assets = assets or Assets()
    source = Path(path).expanduser().resolve(strict=True)
    if not source.is_file():
        raise AssetError("INVALID_INPUT", f"not a regular file: {source}")
    if source.stat().st_size > MAX_FILE_BYTES:
        raise AssetError("LIMIT_EXCEEDED", "input exceeds 128 MiB")
    blob = source.read_bytes()
    assets.sources.append({"path": str(source), "bytes": len(blob),
                           "sha256": hashlib.sha256(blob).hexdigest(), "role": required_role or "primary"})
    previous = (len(assets.models), len(assets.textures), len(assets.motions), len(assets.facial_motion_packages))
    for label, data, hint in _leaves(blob, source.name, assets):
        node = {"label": label, "bytes": len(data), "format": "unknown"}
        assets.nodes.append(node)
        try:
            if hint == "PF":
                from .pf_facial import parse_pf_package
                if assets.facial_motion_packages:
                    raise AssetError("MULTIPLE_FACIAL_PACKAGES", "only one explicitly selected PF facial package is supported per export")
                # Preserve caller file identity for a directly supplied raw/LZ PF.
                # Nested-container leaves retain their own hash plus input provenance.
                direct_source = label in (source.name, source.name + "/LZ")
                try:
                    package = parse_pf_package(blob if direct_source else data)
                except ValueError as exc:
                    raise AssetError("INVALID_PF_PACKAGE", str(exc)) from exc
                if not package["selected_native_profile_verified"]:
                    raise AssetError("UNSUPPORTED_PF_PROFILE", "PF facial playback is limited to the identified ORAS Bulbasaur source profile")
                package["label"] = label
                package["input_source"] = dict(assets.sources[-1])
                assets.facial_motion_packages.append(package)
                node.update({"format": "PF", "entry_count": package["entry_count"],
                             "native_profile_verified": True, "skeletal_clips_added": 0,
                             "source_sha256": package["source_sha256"],
                             "decompressed_sha256": package["decompressed_sha256"]})
            elif hint == "GF1MotionPack":
                from .gf1 import read_pack
                node["format"] = "GF1MotionPack"
                pack = read_pack(data)
                node["skeleton_bones"] = len(pack["skeleton"])
                node["declared_motion_slots"] = pack["declared_slots"]
                node["decoded_motions"] = len(pack["motions"])
                node["motion_errors"] = pack["errors"]
                node["reference"] = pack["reference"]
                if pack.get("native_interpolation_profile"):
                    node["native_interpolation_profile"] = pack["native_interpolation_profile"]
                assets.warnings.extend({**warning, "asset": label} for warning in pack["warnings"])
                for error in pack["errors"]:
                    assets.warnings.append({"code": "GF1_MOTION_PARSE_FAILED", "asset": label,
                                            "motion_index": error["index"], "message": error["error"]})
                for motion in pack["motions"]:
                    assets.motions.append((f"{label}:GF1_{motion.index}", motion))
            elif sniff_gfmodel(data):
                node["format"] = "GFModel"
                assets.models.append(lower_model(read_gfmodel(BinStream(data), label)))
            elif sniff_gftexture(data):
                node["format"] = "GFTexture"
                raw = read_gftexture(BinStream(data))
                if raw.width * raw.height > 16 * 1024 * 1024:
                    raise AssetError("LIMIT_EXCEEDED", "texture exceeds 16 megapixels")
                assets.textures.append(Texture(raw.name, raw.width, raw.height, bytes(raw.to_rgba())))
            elif sniff_bch(data):
                node["format"] = "BCH"
                models, header, contents = _lower_bch_versioned(data)
                node["backward_version"] = header.backward
                node["declared_models"] = contents.models.count
                node["declared_textures"] = contents.textures.count
                node["declared_skeletal_animations"] = contents.skeletal_animations.count
                assets.models.extend(models)
                if any(getattr(model, "pica_config", None) for model in models):
                    assets.warnings.append({"code": "PICA_PLAYER_REQUIRED", "asset": label,
                                            "message": "Source material states and visual animation require the included REA PICA player; standard glTF uses a simpler fallback."})
                for model in models:
                    for error in getattr(model, "pica_errors", []):
                        assets.warnings.append({"code": "PICA_MATERIAL_PARSE_FAILED", "asset": label, "message": str(error)})
                assets.textures.extend(lower_bch_textures(data))
                for index, motion in enumerate(lower_bch_animations(data)):
                    assets.motions.append((motion.name or f"{label}:motion{index}", motion))
                assets.warnings.append({"code": "BCH_REAL_DATA_UNVERIFIED", "asset": label,
                                        "message": "This parser result requires file-specific provenance and validation receipts; it does not establish coverage of all BCH variants or original-game rendering parity."})
                unsupported = {name: contents.dicts[name].count for name in ("material_animations", "visibility_animations", "light_animations", "camera_animations", "fog_animations") if contents.dicts[name].count}
                if assets.visual_motion_packs and not assets.visual_motion_packs[-1].get("errors"):
                    unsupported.pop("material_animations", None)
                    unsupported.pop("visibility_animations", None)
                if unsupported:
                    assets.warnings.append({"code": "UNSUPPORTED_ANIMATION_CHANNELS", "asset": label,
                                            "declared_counts": unsupported,
                                            "message": "Non-skeletal BCH animation data is present and was not exported."})
            else:
                sections = _motion_sections(data)
                if sections is not None:
                    node["format"] = "GFMotion"
                    motion = read_motion(BinStream(data))
                    unsupported = sorted(set(s[0] for s in sections) - {0, 1})
                    if unsupported:
                        assets.warnings.append({"code": "UNSUPPORTED_ANIMATION_CHANNELS", "asset": label,
                                                "sections": unsupported,
                                                "message": "Animation sections other than the supported subheader (0) and skeletal (1) are present and were not exported; section semantics may be unknown."})
                    if motion.bones:
                        assets.motions.append((label, motion))
                    else:
                        assets.warnings.append({"code": "NO_SKELETAL_TRACKS", "asset": label,
                                                "message": "Motion contains no supported skeletal tracks."})
                else:
                    assets.warnings.append({"code": "UNIDENTIFIED_ASSET", "asset": label,
                                            "message": "Leaf format was not recognized; it was not exported."})
        except AssetError:
            raise
        except Exception as exc:
            node["parse_error"] = f"{type(exc).__name__}: {exc}"
            assets.warnings.append({"code": "PARSE_FAILED", "asset": label, "message": node["parse_error"]})
    if (required_role == "motion" and len(assets.motions) == previous[2]
            and len(assets.facial_motion_packages) == previous[3]):
        raise AssetError("NO_SUPPORTED_MOTION", f"requested motion file produced no supported skeletal or identified facial animation: {source.name}")
    if required_role == "texture" and len(assets.textures) == previous[1]:
        raise AssetError("NO_SUPPORTED_TEXTURE", f"requested texture file produced no decoded textures: {source.name}")
    return assets


def describe(assets):
    result = {
        "sources": assets.sources,
        "nodes": assets.nodes,
        "models": [{"name": m.name, "bones": len(m.bones), "meshes": len(m.submeshes),
                    "vertices": sum(len(s.positions) for s in m.submeshes),
                    "triangles": sum(len(s.triangles) for s in m.submeshes),
                    "materials": len(m.materials), "texture_names": m.texture_names}
                   for m in assets.models],
        "textures": [{"name": t.name, "width": t.width, "height": t.height} for t in assets.textures],
        "animations": [{"name": label, "frames": m.frame_count, "looping": m.looping,
                         "bone_tracks": len(m.bones)} for label, m in assets.motions],
        "visual_motion_packages": [{"kind": pack["kind"], "mappings": len(pack["mappings"]),
                                    "errors": pack["errors"], "warnings": pack["warnings"]} for pack in assets.visual_motion_packs],
        "warnings": assets.warnings,
        "limitations": LIMITATIONS,
        "real_game_asset_verified": False,
    }
    if assets.facial_motion_packages:
        result["facial_motion_packages"] = [{"kind": "PF", "label": package["label"],
                                             "source_sha256": package["source_sha256"],
                                             "entry_count": package["entry_count"],
                                             "native_profile_verified": True,
                                             "source_model": "pm0001_00",
                                             "skeletal_clips_added": 0}
                                            for package in assets.facial_motion_packages]
    return result


def dispatch(method, args):
    if method == "capabilities":
        return capabilities()
    if method not in {"inspect", "export"}:
        raise AssetError("UNSUPPORTED_METHOD", f"unknown method: {method}")
    if not isinstance(args.get("path"), str):
        raise AssetError("INVALID_ARGUMENT", "path must be a file path string")
    for key in ("motion_paths", "texture_paths"):
        paths = args.get(key, [])
        if not isinstance(paths, list) or len(paths) > 64 or any(not isinstance(path, str) for path in paths):
            raise AssetError("INVALID_ARGUMENT", f"{key} must be an array of at most 64 path strings")
    assets = read_assets(args["path"])
    if method == "inspect":
        return describe(assets)
    from .glb import export_glb
    for path in args.get("motion_paths", []):
        read_assets(path, assets, "motion")
    for path in args.get("texture_paths", []):
        read_assets(path, assets, "texture")
    return export_glb(assets, args)
