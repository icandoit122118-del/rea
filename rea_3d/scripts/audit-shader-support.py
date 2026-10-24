"""Read-only shader/material support audit; no GPU or original-game execution.

Explicit raw BCH or bounded LZ/PC-family inputs only. Typed metadata is retained
as a sidecar without assigning rendering semantics or changing the exporter.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROJECT / "python"), str(PROJECT / "vendor/n3ds_importer")]
from rea3d_core.pica_material import _Bch, _u32, PicaMaterialError, inspect_bch_materials
from ctr.compress.lz import decompress_lz, sniff_lz

MAX_INPUT = 128 * 1024**2
MAX_EXPANDED = 256 * 1024**2
MAX_ENTRIES = 1024
MAX_VALUES = 16384
MAX_TOTAL_ENTRIES = 4096
MAX_TOTAL_VALUES = 65536
MAX_METADATA_STRING_BYTES = 8 * 1024**2
TYPES = {0: "Integer", 1: "Single", 2: "ASCIIString", 3: "UnicodeString", 4: "BoundingBox", 5: "VertexData"}
sha = lambda value: hashlib.sha256(value).hexdigest()


def require(condition, message):
    if not condition:
        raise PicaMaterialError(message)


def read_string(bch, pointer, encoding):
    """Bounded string read returning exact source bytes including terminator."""
    unit = 2 if encoding == "utf-16-le" else 1
    bch.region(pointer, unit, "strings")
    end = min(pointer + 4096, bch.header.strings_address + bch.header.strings_length)
    for at in range(pointer, end - unit + 1, unit):
        if bch.data[at:at + unit] == bytes(unit):
            raw = bytes(bch.original[pointer:at + unit])
            bch.metadata_string_bytes = getattr(bch, "metadata_string_bytes", 0) + len(raw)
            require(bch.metadata_string_bytes <= MAX_METADATA_STRING_BYTES, "metadata string byte budget exceeded")
            return {"source_offset": pointer, "raw_source_hex": raw.hex(),
                    "value": bytes(bch.data[pointer:at]).decode(encoding, errors="strict")}
    raise PicaMaterialError("unterminated metadata string")


def read_metadata(bch, field_at):
    """BCH0x21 material params+0x10c -> dictionary -> inline 12-byte items.

    The raw original fields and relocated pointers are both retained. Unknown or
    variable complex types retain their header only and explicitly remain partial.
    """
    bch.region(field_at, 4)
    pointer = _u32(bch.data, field_at)
    out = {"schema": "rea3d.bch.user_metadata.sidecar.v1", "pointer_source_offset": field_at,
           "pointer_raw_source_hex": bch.original[field_at:field_at + 4].hex(),
           "dictionary_offset": pointer, "entries": [], "complete": True,
           "rendering_semantics_verified": False, "runtime_effects_applied": False}
    if not pointer:
        return out
    bch.region(pointer, 12)
    values, count, tree = struct.unpack_from("<III", bch.data, pointer)
    require(count <= MAX_ENTRIES, "metadata dictionary entry budget exceeded")
    bch.metadata_total_entries = getattr(bch, "metadata_total_entries", 0) + count
    require(bch.metadata_total_entries <= MAX_TOTAL_ENTRIES, "total metadata entry budget exceeded")
    if count:
        bch.region(values, count * 12)
    out.update(dictionary_raw_source_hex=bch.original[pointer:pointer + 12].hex(), count=count,
               values_offset=values, patricia_tree_offset=tree)
    total_values = 0
    for i in range(count):
        at = values + i * 12
        name_at, kind, value_count, value_at = struct.unpack_from("<IHHI", bch.data, at)
        total_values += value_count
        require(total_values <= MAX_VALUES, "metadata total value budget exceeded")
        bch.metadata_total_values = getattr(bch, "metadata_total_values", 0) + value_count
        require(bch.metadata_total_values <= MAX_TOTAL_VALUES, "cross-material metadata value budget exceeded")
        entry = {"index": i, "source_offset": at, "name": bch.string(name_at),
                 "name_source_offset": name_at, "type_code": kind,
                 "type": TYPES.get(kind, "Unknown"), "count": value_count,
                 "values_offset": value_at, "raw_header_hex": bch.original[at:at + 12].hex(),
                 "decoded": False}
        if kind in (0, 1, 2, 3, 4):
            size = value_count * (60 if kind == 4 else 4)
            if size:
                bch.region(value_at, size)
            raw = bytes(bch.original[value_at:value_at + size])
            entry["raw_value_hex"] = raw.hex()
            if kind == 0:
                entry["values"] = list(struct.unpack_from("<" + "i" * value_count, bch.data, value_at))
            elif kind in (1, 4):
                width = 15 if kind == 4 else 1
                numbers = list(struct.unpack_from("<" + "f" * (value_count * width), bch.data, value_at))
                entry["values"] = [x if math.isfinite(x) else None for x in numbers]
                entry["nonfinite_float_indices"] = [j for j, x in enumerate(numbers) if not math.isfinite(x)]
                if kind == 4:
                    entry["layout"] = "15 floats per item: center3, orientation9, size3; no render interpretation"
            else:
                encoding = "ascii" if kind == 2 else "utf-16-le"
                entry["strings"] = [read_string(bch, _u32(bch.data, value_at + j * 4), encoding)
                                    for j in range(value_count)]
                entry["values"] = [x["value"] for x in entry["strings"]]
            entry["decoded"] = True
        else:
            # A VertexData entry owns nested descriptors and raw buffers; treating
            # it as a float array would lose references or read unbounded memory.
            if value_count:
                bch.region(value_at, 1)
            entry["limitation"] = "header retained; complex/unknown value layout not followed"
            out["complete"] = False
        out["entries"].append(entry)
    return out


def audit_bch(blob):
    bch = _Bch(blob)
    report = inspect_bch_materials(blob)
    for model in report["models"]:
        for material in model["materials"]:
            material["user_metadata_sidecar"] = read_metadata(bch, material["source_offsets"]["params"] + 0x10C)
    report["metadata_scope"] = "sidecar only; no exporter/player mutation or shader-effect interpretation"
    return report


def bch_leaves(blob, label, budget, depth=0):
    require(depth <= 8, "input nesting exceeds eight")
    if sniff_lz(blob[:16]):
        require(len(blob) >= 4, "short LZ header")
        size = int.from_bytes(blob[1:4], "little")
        if size == 0:
            require(len(blob) >= 8, "short extended LZ header")
            size = int.from_bytes(blob[4:8], "little")
        require(size <= MAX_EXPANDED - budget[0], "expanded byte budget exceeded")
        decoded = decompress_lz(blob)
        require(len(decoded) == size, "LZ decoded length differs from header")
        budget[0] += size
        yield from bch_leaves(decoded, label + "/LZ", budget, depth + 1)
    elif blob[:2] in (b"PC", b"PS", b"PB", b"PK", b"PT", b"PF"):
        require(len(blob) >= 8, "short package header")
        count = struct.unpack_from("<H", blob, 2)[0]
        require(count <= 4096 and 4 + (count + 1) * 4 <= len(blob), "invalid package count")
        offsets = struct.unpack_from("<" + "I" * (count + 1), blob, 4)
        require(offsets[0] >= 4 + (count + 1) * 4 and offsets[-1] <= len(blob)
                and all(a <= b for a, b in zip(offsets, offsets[1:])), "invalid package offsets")
        for i, (a, b) in enumerate(zip(offsets, offsets[1:])):
            if b > a:
                yield from bch_leaves(blob[a:b], label + f"/{blob[:2].decode()}[{i}]", budget, depth + 1)
    elif blob[:4] == b"BCH\0":
        budget[1] += 1
        require(budget[1] <= 4096, "BCH leaf budget exceeded")
        yield label, blob


def source_support_matrix():
    """Source-level evidence only. No synthetic result becomes GPU parity."""
    rows = [
        ("skeletal skinning", "implemented", "custom vertex shader weighted uBoneMatrices; root/world parity separate"),
        ("UV transform and texture selection", "implemented", "evaluateMaterialFrame + source matrix + three samplers"),
        ("six TEV stages, constants and buffers", "implemented", "createPicaFragment generates decoded combiners"),
        ("alpha test", "implemented", "decoded function/reference generate fragment discard"),
        ("normal/tangent bump and quaternion basis", "implemented", "source bump flags select texture and basis rotation"),
        ("reflection/distribution/fresnel LUTs", "partial", "source base LUT values; original difference interpolation/precision unverified"),
        ("material/mesh visibility animation", "implemented", "evaluateMaterialFrame drives visible and uniforms"),
        ("face culling", "implemented", "source culling -> Three material.side"),
        ("depth test enable/write", "implemented", "source booleans -> Three material flags"),
        ("depth comparison function", "decoded_not_applied", "source depth_function retained; Three material depthFunc not set"),
        ("color channel write mask", "decoded_not_applied", "source per-channel mask retained; no matching GL state application"),
        ("alpha/color blending", "decoded_not_applied", "Three player forces transparent=false and NoBlending"),
        ("stencil and logical operation", "retained_not_applied", "raw stencil registers and logical op retained; not mapped to Three"),
        ("polygon offset", "decoded_not_applied", "source flag/unit retained; no Three polygonOffset setup"),
        ("fog and original light sets", "not_reconstructed", "flags/index retained; fixed preview ambient/direction/color used"),
        ("original mip levels, LOD bias and border color", "partial", "decoded sampler fields; preview generates mipmaps and approximates wrap"),
        ("texture unit3/procedural texture", "unsupported", "fragment exposes only texture0,1,2; source Texture3 expression rejected"),
        ("material color scale", "decoded_not_applied", "color_scale retained by parser; no corresponding player uniform or scalar multiply"),
        ("external vertex/geometry shader_reference", "not_executed", "reference string retained; custom basis/skinning shader used"),
        ("Edge/Rim/Phong user metadata", "sidecar_only", "params+0x10c omitted by existing exporter; this audit retains typed/raw fields without effects"),
        ("outline/shadow/postprocess/multiple passes", "not_reconstructed", "no source pass graph, edge buffer or separate original pass execution in player"),
    ]
    return [{"feature": name, "status": status, "evidence": evidence,
             "evidence_level": "current_source_inspection", "original_game_parity_verified": False}
            for name, status, evidence in rows]


def source_manifest():
    paths = ["scripts/audit-shader-support.py", "python/rea3d_core/pica_material.py", "python/rea3d_core/runtime_export.py",
             "preview/pica-runtime.js", "preview/pica-player.js",
             "research/SPICA/SPICA/Formats/CtrH3D/H3DMetaDataValue.cs",
             "research/SPICA/SPICA/Formats/CtrH3D/H3DMetaDataType.cs",
             "research/SPICA/SPICA/Formats/CtrH3D/Model/Material/H3DMaterialParams.cs",
             "research/Ohana3DS-Rebirth/Ohana3DS Rebirth/Ohana/Models/BCH.cs"]
    return [{"path": str(PROJECT / p), "bytes": (PROJECT / p).stat().st_size,
             "sha256": sha((PROJECT / p).read_bytes())} for p in paths]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", action="append", default=[])
    p.add_argument("--output-dir", required=True)
    a = p.parse_args()
    out = Path(a.output_dir)
    require(out.is_absolute() and not out.exists(), "new absolute output directory required")
    inputs, parsed = [], []
    for value in a.input:
        path = Path(value)
        require(path.is_absolute() and path.is_file() and not path.is_symlink(), "explicit regular input required")
        require(path.stat().st_size <= MAX_INPUT, "input exceeds128MiB")
        blob = path.read_bytes()
        inputs.append({"path": str(path), "bytes": len(blob), "sha256": sha(blob)})
        for label, leaf in bch_leaves(blob, path.name, [0, 0]):
            parsed.append({"input": str(path), "label": label, "bytes": len(leaf), "sha256": sha(leaf),
                           "report": audit_bch(leaf)})
    result = {"schema": "rea3d.shader.support.audit.v1", "inputs": inputs, "bch_leaves": parsed,
              "support_matrix": source_support_matrix(), "source_manifest": source_manifest(),
              "original_shader_bytecode_executed": False, "game_executed": False,
              "gpu_shader_compiled": False, "gpu_parity_verified": False,
              "native_renderpass_semantics_newly_verified": False,
              "limits": ["No native process access, writes or game launch.",
                         "Metadata names and values do not establish render-pass semantics.",
                         "Source support matrix is not an executed GPU feature corpus."]}
    out.mkdir(parents=True, exist_ok=False)
    target = out / "shader-support-audit.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(target), "sha256": sha(target.read_bytes()),
                      "inputs": len(inputs), "bch_leaves": len(parsed), "matrix_rows": len(result["support_matrix"])}))


if __name__ == "__main__":
    main()
