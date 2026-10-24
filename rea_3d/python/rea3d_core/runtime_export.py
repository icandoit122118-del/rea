"""Embed source PICA states and paired visual clips without claiming standard glTF support."""
from __future__ import annotations
import re


def make_runtime(document, model, assets, animations, fps):
    config = getattr(model, "pica_config", None)
    facial_packages = getattr(assets, "facial_motion_packages", [])
    if facial_packages and (len(facial_packages) != 1 or model.name != "pm0001_00" or not config):
        from .bridge import AssetError
        raise AssetError("PF_MODEL_INCOMPATIBLE", "the selected PF requires exactly one identified package and the pm0001_00 PICA model")
    if not config:
        return None
    images = document.doc.get("images", [])
    texture_bindings = {}
    for image_index, image in enumerate(images):
        textures = document.doc.setdefault("textures", [])
        texture_index = next((i for i, texture in enumerate(textures) if texture.get("source") == image_index), None)
        if texture_index is None:
            texture_index = len(textures)
            textures.append({"source": image_index})
        texture_bindings[image["name"]] = texture_index
    nodes = {}
    for mesh in config.get("mesh_visibility", {}).get("meshes", []):
        name = mesh.get("node_name")
        if name:
            nodes[name] = {"name": name, "default_visible": mesh.get("default_visible", True)}
    clips = []
    for animation in animations:
        label = animation["name"]
        match = re.search(r"/([A-Z]{2})\[0\]:GF1_(\d+)$", label)
        binding = {"name": label, "source_name": None, "role": "primary",
                   "material_animations": [], "visibility_animations": [], "warnings": []}
        if match:
            prefix, kind, slot = label[:match.start()], match[1], int(match[2])
            packs = [pack for pack in assets.visual_motion_packs if pack.get("kind") == kind and pack.get("label") == prefix]
            for pack in packs:
                matches = [item for item in pack.get("mappings", []) if item["source_slot"] == slot]
                if len(matches) == 1:
                    item = matches[0]
                    binding.update({"source_name": item.get("clip_name"), "source_slot": slot,
                                    "source_package": kind,
                                    "material_animations": item.get("material_animations", []),
                                    "visibility_animations": item.get("visibility_animation", []),
                                    "warnings": item.get("warnings", [])})
                    if item.get("role") == "auxiliary_pose":
                        binding.update({"role": "auxiliary_pose", "auxiliary_pose": item["auxiliary_pose"],
                                        "association_kind": item["association_kind"],
                                        "source_name": f"{kind}_shared_auxiliary_jaw_pose"})
        clips.append(binding)
    runtime = {"schema": "rea.pica.runtime.v1", "source_model": model.name, "source_fps": fps,
            "materials": config.get("materials", []), "mesh_nodes": list(nodes.values()),
            "texture_bindings": texture_bindings, "luts": getattr(model, "pica_luts", []),
            "clips": clips, "source_reports": {"material_errors": getattr(model, "pica_errors", []),
                                               "visual_pack_errors": [pack.get("errors", []) for pack in assets.visual_motion_packs]},
            "playback_requirement": "REA PICA player; standard glTF viewers only play the skeletal fallback",
            "original_game_comparison_verified": False}
    if facial_packages:
        from .pf_facial import make_facial_runtime
        try:
            runtime["facial_animation"] = make_facial_runtime(facial_packages[0], model.name)
        except ValueError as exc:
            from .bridge import AssetError
            raise AssetError("UNSUPPORTED_PF_PROFILE", str(exc)) from exc
    native_profiles = [node["native_interpolation_profile"] for node in getattr(assets, "nodes", [])
                       if node.get("native_interpolation_profile")]
    if native_profiles:
        runtime["source_reports"]["gf1_native_interpolation_profiles"] = native_profiles
    return runtime
