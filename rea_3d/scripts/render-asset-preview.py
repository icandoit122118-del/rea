"""Blender background GLB import, deformation evidence, and bounded PNG previews.

Usage:
  blender --background --factory-startup --python render-asset-preview.py -- \
    --input ABSOLUTE_MODEL.glb --output-dir NEW_DIRECTORY_ON_APPROVED_D_ROOT

The source is never saved or modified. Generated files are restricted to the
task-specific D: work root. Renders use neutral Blender lighting, not game shaders.
"""

import argparse
from array import array
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import sys
import time
import traceback

import bpy
from mathutils import Vector


WORK_ROOT = Path("D:/REA3D-ORAS-20261023-1792751073625")
MAX_GLB_BYTES = 256 * 1024 * 1024
EPSILON = 1e-6


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_glb_document(path):
    size = path.stat().st_size
    if size < 20 or size > MAX_GLB_BYTES:
        raise ValueError("Input GLB must be between 20 bytes and 256 MiB")
    with path.open("rb") as stream:
        header = stream.read(20)
        magic, version, declared, json_length, json_type = struct.unpack("<5I", header)
        if magic != 0x46546C67 or version != 2 or declared != size:
            raise ValueError("Input must have a valid GLB v2 header and declared size")
        if json_type != 0x4E4F534A or json_length > 32 * 1024 * 1024 or 20 + json_length > size:
            raise ValueError("Input must have a bounded first JSON chunk")
        document = json.loads(stream.read(json_length).decode("utf-8"))
    if document.get("asset", {}).get("version") != "2.0":
        raise ValueError("Input glTF version must be 2.0")
    stack = [document]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "uri" and (not isinstance(child, str) or not child.startswith("data:")):
                    raise ValueError("External resources are refused; use a self-contained GLB")
                if isinstance(child, (dict, list)):
                    stack.append(child)
        elif isinstance(value, list):
            stack.extend(item for item in value if isinstance(item, (dict, list)))
    return document


def new_output_directory(raw):
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise ValueError("Output directory must be absolute")
    root = WORK_ROOT.resolve(strict=True)
    resolved = candidate.resolve(strict=False)
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError("All outputs must stay within " + str(root))
    if candidate.exists() or candidate.is_symlink():
        raise ValueError("Output directory must be new; existing paths are never overwritten")
    resolved.mkdir(parents=True, exist_ok=False)
    temporary = resolved / "tmp"
    temporary.mkdir()
    for key in ("TMP", "TEMP", "TMPDIR"):
        os.environ[key] = str(temporary)
    bpy.context.preferences.filepaths.temporary_directory = str(temporary)
    return resolved


def json_write(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def animation_owners(imported):
    owners = []
    seen = set()

    def add(owner):
        if owner is None or owner.as_pointer() in seen:
            return
        seen.add(owner.as_pointer())
        if getattr(owner, "animation_data", None):
            owners.append(owner)

    for obj in imported:
        add(obj)
        add(obj.data)
        if obj.type == "MESH":
            add(obj.data.shape_keys)
    for material in bpy.data.materials:
        add(material)
        add(material.node_tree)
    return owners


def enumerate_clips(owners, source):
    clips = {}
    for owner in owners:
        for track in owner.animation_data.nla_tracks:
            for strip in track.strips:
                if strip.action is None:
                    continue
                entry = clips.setdefault(track.name, {"name": track.name, "actions": [], "frame_start": float("inf"), "frame_end": -float("inf")})
                start, end = strip.action.frame_range
                entry["frame_start"] = min(entry["frame_start"], float(start))
                entry["frame_end"] = max(entry["frame_end"], float(end))
                entry["actions"].append({
                    "owner": owner.name, "owner_type": owner.id_type,
                    "action": strip.action.name,
                    "slot": getattr(strip.action_slot, "identifier", None),
                })
    # NLA tracks are created in reverse order. Use the installed importer's
    # exact UTF-8 truncation/duplicate-name logic to map back to GLB indices.
    from io_scene_gltf2.blender.imp.blender_gltf import BlenderGlTF
    track_names = set()
    ordered = []
    for source_index, animation in enumerate(source.get("animations", [])):
        desired = animation.get("name") or "Anim_%d" % source_index
        name = BlenderGlTF.find_unused_name(track_names, desired)
        track_names.add(name)
        if name in clips:
            entry = clips.pop(name)
            entry["source_animation_index"] = source_index
            entry["source_animation_name"] = animation.get("name")
            ordered.append(entry)
    for entry in clips.values():
        entry["source_animation_index"] = None
        entry["source_animation_name"] = None
        ordered.append(entry)
    return ordered


def capture_defaults(imported):
    return {
        "objects": [(obj, obj.matrix_basis.copy()) for obj in imported],
        "bones": [(bone, bone.matrix_basis.copy()) for obj in imported if obj.type == "ARMATURE" for bone in obj.pose.bones],
        "shapes": [(key, key.value) for obj in imported if obj.type == "MESH" and obj.data.shape_keys for key in obj.data.shape_keys.key_blocks],
    }


def activate_clip(owners, imported, defaults, name):
    for owner in owners:
        animation = owner.animation_data
        animation.action = None
        for track in animation.nla_tracks:
            track.mute = True
    for obj, matrix in defaults["objects"]:
        obj.matrix_basis = matrix
    for bone, matrix in defaults["bones"]:
        bone.matrix_basis = matrix
    for key, value in defaults["shapes"]:
        key.value = value
    for obj in imported:
        if obj.type == "ARMATURE":
            obj.data.pose_position = "REST" if name is None else "POSE"
    if name is not None:
        for owner in owners:
            for track in owner.animation_data.nla_tracks:
                if track.name != name or not track.strips:
                    continue
                # Blender 4.4+ layered actions require their imported slot as well.
                strip = track.strips[0]
                owner.animation_data.action = strip.action
                owner.animation_data.action_slot = strip.action_slot
                break
    bpy.context.view_layer.update()


def frame_set(frame):
    base = math.floor(frame)
    bpy.context.scene.frame_set(base, subframe=frame - base)
    bpy.context.view_layer.update()


def evaluated_snapshot(meshes, armatures):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    points = array("d")
    vertex_counts = []
    minimum = [float("inf")] * 3
    maximum = [-float("inf")] * 3
    vertex_examples = []
    mesh_bounds = []
    for obj in meshes:
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            vertex_counts.append({"mesh": obj.name, "vertices": len(mesh.vertices)})
            examples = []
            mesh_minimum = [float("inf")] * 3
            mesh_maximum = [-float("inf")] * 3
            for index, vertex in enumerate(mesh.vertices):
                world = evaluated.matrix_world @ vertex.co
                values = tuple(float(v) for v in world)
                if not all(math.isfinite(v) for v in values):
                    raise ValueError("Non-finite evaluated vertex coordinate")
                points.extend(values)
                for axis in range(3):
                    minimum[axis] = min(minimum[axis], values[axis])
                    maximum[axis] = max(maximum[axis], values[axis])
                    mesh_minimum[axis] = min(mesh_minimum[axis], values[axis])
                    mesh_maximum[axis] = max(mesh_maximum[axis], values[axis])
                if index < 4:
                    examples.append(list(values))
            vertex_examples.append({"mesh": obj.name, "first_vertices_world": examples})
            if len(mesh.vertices):
                mesh_bounds.append({"mesh": obj.name, "min": mesh_minimum, "max": mesh_maximum})
        finally:
            evaluated.to_mesh_clear()
    if not points:
        raise ValueError("The imported model has no evaluable mesh vertices")
    bone_matrices = {}
    bone_scales = {}
    bone_translations = {}
    for obj in armatures:
        evaluated = obj.evaluated_get(depsgraph)
        for bone in evaluated.pose.bones:
            matrix = evaluated.matrix_world @ bone.matrix
            key = obj.name + "/" + bone.name
            bone_matrices[key] = [float(matrix[row][column]) for row in range(4) for column in range(4)]
            bone_scales[key] = [float(value) for value in matrix.to_scale()]
            bone_translations[key] = [float(value) for value in matrix.translation]
    summary = {
        "evaluated_vertex_count": len(points) // 3,
        "mesh_vertex_counts": vertex_counts,
        "evaluated_vertices_sha256": hashlib.sha256(points.tobytes()).hexdigest(),
        "vertex_hash_encoding": "little-endian float64 world XYZ, mesh/object iteration order",
        "bounds_world": {"min": minimum, "max": maximum},
        "mesh_bounds_world": mesh_bounds,
        "vertex_examples": vertex_examples[:6],
        "bone_matrix_count": len(bone_matrices),
        "bone_matrices_sha256": hashlib.sha256(json.dumps(bone_matrices, sort_keys=True, allow_nan=False).encode()).hexdigest(),
        "bone_matrix_examples": dict(list(bone_matrices.items())[:8]),
        "bone_scales_world": bone_scales,
        "bone_translations_world": bone_translations,
    }
    return {"summary": summary, "points": points, "bones": bone_matrices}


def difference(current, reference):
    before, after = reference["points"], current["points"]
    geometry_comparable = reference["summary"]["mesh_vertex_counts"] == current["summary"]["mesh_vertex_counts"]
    maximum = 0.0
    moved = 0
    if geometry_comparable:
        for index in range(0, len(after), 3):
            distance = math.sqrt(sum((after[index + axis] - before[index + axis]) ** 2 for axis in range(3)))
            maximum = max(maximum, distance)
            moved += distance > EPSILON
    matrices_comparable = set(reference["bones"]) == set(current["bones"])
    changed_bones = 0
    max_matrix_delta = 0.0
    max_bone_translation = 0.0
    if matrices_comparable:
        for key, matrix in current["bones"].items():
            old = reference["bones"][key]
            delta = max(abs(a - b) for a, b in zip(matrix, old))
            max_matrix_delta = max(max_matrix_delta, delta)
            changed_bones += delta > EPSILON
            max_bone_translation = max(max_bone_translation, math.sqrt(sum((matrix[index] - old[index]) ** 2 for index in (3, 7, 11))))
    return {
        "epsilon": EPSILON,
        "vertex_topology_comparable": geometry_comparable,
        "moved_vertices": moved if geometry_comparable else None,
        "max_vertex_displacement_world": maximum if geometry_comparable else None,
        "bone_sets_comparable": matrices_comparable,
        "changed_bones": changed_bones if matrices_comparable else None,
        "max_bone_matrix_element_delta": max_matrix_delta if matrices_comparable else None,
        "max_bone_translation_world": max_bone_translation if matrices_comparable else None,
    }


def import_statistics(imported, clips, source):
    meshes = [obj for obj in imported if obj.type == "MESH"]
    armatures = [obj for obj in imported if obj.type == "ARMATURE"]
    materials = {slot.material for obj in meshes for slot in obj.material_slots if slot.material}
    texture_nodes = []
    for material in materials:
        if not material.node_tree:
            continue
        for node in material.node_tree.nodes:
            if node.type != "TEX_IMAGE":
                continue
            image = node.image
            texture_nodes.append({
                "material": material.name, "node": node.name,
                "image": image.name if image else None,
                "size": list(image.size) if image else None,
                "has_data": bool(image.has_data) if image else False,
                "packed": bool(image.packed_file) if image else False,
                "colorspace": image.colorspace_settings.name if image else None,
                "outgoing_links": [{"output": output.name, "target_node": link.to_node.name, "target_socket": link.to_socket.name} for output in node.outputs for link in output.links],
            })
    images = {node["image"] for node in texture_nodes if node["image"] is not None}
    return {
        "objects": len(imported), "mesh_objects": len(meshes),
        "mesh_vertices": sum(len(obj.data.vertices) for obj in meshes),
        "mesh_triangles": sum(sum(max(0, len(poly.vertices) - 2) for poly in obj.data.polygons) for obj in meshes),
        "armature_objects": len(armatures),
        "bones": sum(len(obj.data.bones) for obj in armatures),
        "mesh_armature_modifiers": sum(mod.type == "ARMATURE" for obj in meshes for mod in obj.modifiers),
        "mesh_uv_layers": {obj.name: [layer.name for layer in obj.data.uv_layers] for obj in meshes},
        "materials": len(materials), "material_names": sorted(material.name for material in materials),
        "texture_images_referenced_by_materials": len(images), "image_texture_nodes": texture_nodes,
        "connected_image_texture_nodes": sum(bool(node["outgoing_links"]) for node in texture_nodes),
        "source_glb_animation_count": len(source.get("animations", [])),
        "source_glb_animation_names": [clip.get("name", "clip_" + str(index)) for index, clip in enumerate(source.get("animations", []))],
        "imported_clip_count": len(clips),
        "imported_action_count": len(bpy.data.actions),
        "clips": clips,
    }


def setup_render(bounds, size):
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x = size
    scene.render.resolution_y = size
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = False
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 4
    if hasattr(scene, "eevee") and hasattr(scene.eevee, "taa_render_samples"):
        scene.eevee.taa_render_samples = 16
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0
    scene.view_settings.gamma = 1
    world = bpy.data.worlds.new("Verification neutral world")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.055, 0.07, 0.10, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.65
    scene.world = world
    lower, upper = Vector(bounds["min"]), Vector(bounds["max"])
    center = (lower + upper) / 2
    radius = max((upper - lower).length / 2, 0.01)
    camera_data = bpy.data.cameras.new("Verification camera")
    camera = bpy.data.objects.new("Verification camera", camera_data)
    scene.collection.objects.link(camera)
    camera_data.lens = 48
    distance = radius / math.sin(min(camera_data.angle_x, camera_data.angle_y) / 2) * 1.18
    camera.location = center + Vector((1.5, -2.8, 1.15)).normalized() * distance
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera_data.clip_start = max(radius / 1000, 0.0001)
    camera_data.clip_end = max(distance + radius * 100, 100)
    scene.camera = camera
    for name, direction, strength, color in (
        ("Key", (3, -4, 6), 850, (1, .96, .90)),
        ("Fill", (-4, -2, 2), 550, (.78, .88, 1)),
        ("Rim", (1, 4, 4), 950, (.88, .94, 1)),
    ):
        data = bpy.data.lights.new("Verification " + name, "AREA")
        obj = bpy.data.objects.new("Verification " + name, data)
        scene.collection.objects.link(obj)
        obj.location = center + Vector(direction) * radius
        obj.rotation_euler = (center - obj.location).to_track_quat("-Z", "Y").to_euler()
        data.energy = strength * radius * radius
        data.shape = "DISK"
        data.size = 4 * radius
        data.color = color
    return {"camera_location": list(camera.location), "look_at": list(center), "bounds_union": bounds, "radius": radius, "lighting": "Neutral Blender three-area-light setup; original-game shader parity is not asserted", "resolution": [size, size], "engine": scene.render.engine}


def main():
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--clip-index", type=int, default=0)
    parser.add_argument("--size", type=int, default=448)
    parser.add_argument("--evaluate-only", action="store_true", help="Import and evaluate geometry/bones, without generating PNGs")
    opts = parser.parse_args(args)
    if not 384 <= opts.size <= 640:
        raise ValueError("Render size must be between 384 and 640 pixels")
    if opts.clip_index < 0:
        raise ValueError("clip-index must be nonnegative")
    source = Path(opts.input)
    if not source.is_absolute() or source.suffix.lower() != ".glb":
        raise ValueError("Input must be an absolute GLB path")
    source = source.resolve(strict=True)
    if not source.is_file():
        raise ValueError("Input must be a regular file")
    document = read_glb_document(source)
    output = new_output_directory(opts.output_dir)
    started = time.time()
    source_before = digest_file(source)
    receipt = {
        "schema": "rea3d-blender-preview-v1", "ok": False,
        "execution": "blender_background_import_and_evaluate" if opts.evaluate_only else "blender_background_import_evaluate_and_render",
        "blender_version": bpy.app.version_string,
        "source": {"path": str(source), "bytes": source.stat().st_size, "sha256_before": source_before},
        "output_directory": str(output),
        "scope": {
            "actual_glb_import": False, "actual_evaluated_mesh_and_bones": False,
            "actual_rendered_png": False, "original_game_visual_parity": False,
            "game_execution": False,
            "limitations": [
                "Neutral Blender lighting and imported glTF materials are used; original-game shaders are not reconstructed.",
                "Only the selected clip is sampled; zero start/mid/end differences do not prove the entire clip is static.",
                "Rest uses armature REST mode with imported object transforms and animation detached.",
                "Vertex differences measure actual evaluated geometry; bone changes alone do not prove visible deformation.",
            ],
        },
    }
    try:
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        scene = bpy.context.scene
        scene.render.fps = 30
        scene.render.fps_base = 1
        result = bpy.ops.import_scene.gltf(filepath=str(source), import_pack_images=True)
        if "FINISHED" not in result:
            raise RuntimeError("Blender GLB importer did not finish")
        receipt["scope"]["actual_glb_import"] = True
        imported_all = list(scene.objects)
        # Blender's glTF importer adds hidden Icosphere meshes as bone widgets.
        # Exclude those generated helpers from asset counts, bounds and vertices.
        bone_widget_ids = {bone.custom_shape.as_pointer() for obj in imported_all if obj.type == "ARMATURE" for bone in obj.pose.bones if bone.custom_shape is not None}
        imported = [obj for obj in imported_all if obj.as_pointer() not in bone_widget_ids]
        meshes = [obj for obj in imported if obj.type == "MESH"]
        armatures = [obj for obj in imported if obj.type == "ARMATURE"]
        owners = animation_owners(imported)
        clips = enumerate_clips(owners, document)
        defaults = capture_defaults(imported)
        receipt["import"] = import_statistics(imported, clips, document)
        receipt["import"]["scene_objects_including_generated_helpers"] = len(imported_all)
        receipt["import"]["excluded_blender_bone_widget_objects"] = [obj.name for obj in imported_all if obj.as_pointer() in bone_widget_ids]
        activate_clip(owners, imported, defaults, None)
        frame_set(0)
        rest = evaluated_snapshot(meshes, armatures)
        receipt["scope"]["actual_evaluated_mesh_and_bones"] = True
        receipt["rest"] = rest["summary"]
        selected = next((clip for clip in clips if clip["source_animation_index"] == opts.clip_index), None)
        if document.get("animations") and selected is None:
            raise ValueError("Requested source GLB animation index has no matching imported NLA track")
        receipt["selected_clip"] = selected
        receipt["samples"] = []
        bounds = {axis: list(rest["summary"]["bounds_world"][axis]) for axis in ("min", "max")}
        if selected:
            activate_clip(owners, imported, defaults, selected["name"])
            start, end = selected["frame_start"], selected["frame_end"]
            first = None
            previous = None
            for label, frame in (("start", start), ("middle", (start + end) / 2), ("end", end)):
                frame_set(frame)
                current = evaluated_snapshot(meshes, armatures)
                if first is None:
                    first = current
                sample = {"label": label, "frame": frame, "seconds": frame / scene.render.fps,
                          **current["summary"], "difference_from_rest": difference(current, rest),
                          "difference_from_clip_start": difference(current, first)}
                if previous is not None:
                    sample["difference_from_previous_sample"] = difference(current, previous)
                receipt["samples"].append(sample)
                for axis in range(3):
                    bounds["min"][axis] = min(bounds["min"][axis], current["summary"]["bounds_world"]["min"][axis])
                    bounds["max"][axis] = max(bounds["max"][axis], current["summary"]["bounds_world"]["max"][axis])
                previous = current
        renders = []
        if not opts.evaluate_only:
            receipt["render_setup"] = setup_render(bounds, opts.size)
            activate_clip(owners, imported, defaults, None)
            frame_set(0)
            for label, frame in [("rest", 0)] + [("clip_%03d_%s" % (opts.clip_index, sample["label"]), sample["frame"]) for sample in receipt["samples"]]:
                if label != "rest":
                    activate_clip(owners, imported, defaults, selected["name"])
                frame_set(frame)
                image = output / (label + ".png")
                scene.render.filepath = str(image)
                bpy.ops.render.render(write_still=True)
                if not image.is_file() or image.stat().st_size <= 0:
                    raise RuntimeError("Blender did not write the expected PNG")
                renders.append({"label": label, "frame": frame, "path": str(image), "bytes": image.stat().st_size, "sha256": digest_file(image)})
        receipt["renders"] = renders
        receipt["scope"]["actual_rendered_png"] = bool(renders)
        receipt["source"]["sha256_after"] = digest_file(source)
        receipt["source"]["unchanged"] = receipt["source"]["sha256_after"] == source_before
        if not receipt["source"]["unchanged"]:
            raise RuntimeError("Input source hash changed during verification")
        receipt["sampled_motion_evidence"] = {
            "selected_clip_present": selected is not None,
            "any_evaluated_vertices_changed_vs_start": any((sample["difference_from_clip_start"]["moved_vertices"] or 0) > 0 for sample in receipt["samples"]),
            "any_bone_matrices_changed_vs_start": any((sample["difference_from_clip_start"]["changed_bones"] or 0) > 0 for sample in receipt["samples"]),
            "comparison_boundary": "Only start, middle and end of the selected imported clip; no original-game timing or shader comparison",
        }
        receipt["elapsed_seconds"] = time.time() - started
        receipt["ok"] = True
        receipt_path = output / "render-verification.json"
        json_write(receipt_path, receipt)
        print("REA3D_RENDER_RESULT " + json.dumps({"ok": True, "receipt": str(receipt_path), "renders": [item["path"] for item in renders], "imported_clip_count": len(clips), "motion_evidence": receipt["sampled_motion_evidence"]}, ensure_ascii=False))
    except Exception as error:
        receipt["error"] = str(error)
        receipt["traceback"] = traceback.format_exc()
        receipt["elapsed_seconds"] = time.time() - started
        receipt["source"]["sha256_after"] = digest_file(source)
        receipt["source"]["unchanged"] = receipt["source"]["sha256_after"] == source_before
        json_write(output / "render-error.json", receipt)
        raise


if __name__ == "__main__":
    main()
