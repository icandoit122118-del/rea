"""Append the source-identified PF contract to a new isolated REA player GLB.

SPDX-License-Identifier: GPL-3.0-or-later
Inputs are read-only. A new absolute output directory is mandatory. The binary
GLB chunks, primary animations and existing runtime fields are preserved.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import struct
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "python"))
from rea3d_core.bch_animation import serialize
from rea3d_core.pf_facial import make_facial_runtime, parse_pf_package, sample_facial_runtime

MAX_GLB_BYTES = 1024 * 1024 * 1024


def read_glb(data):
    if len(data) < 20 or len(data) > MAX_GLB_BYTES:
        raise ValueError("GLB length exceeds the supported range")
    magic, version, length = struct.unpack_from("<4sII", data)
    if magic != b"glTF" or version != 2 or length != len(data):
        raise ValueError("expected a complete GLB version 2 file")
    chunks, offset = [], 12
    while offset < len(data):
        if offset + 8 > len(data):
            raise ValueError("truncated GLB chunk header")
        size, kind = struct.unpack_from("<II", data, offset)
        offset += 8
        if size % 4 or offset + size > len(data):
            raise ValueError("invalid GLB chunk bounds")
        chunks.append((kind, data[offset:offset + size]))
        offset += size
    if not chunks or chunks[0][0] != 0x4E4F534A or len(chunks[0][1]) > 128 * 1024 * 1024:
        raise ValueError("GLB must start with a bounded JSON chunk")
    if any(kind == 0x4E4F534A for kind, _ in chunks[1:]):
        raise ValueError("multiple GLB JSON chunks are unsupported")
    return json.loads(chunks[0][1]), chunks[1:]


def write_glb(document, chunks):
    payload = json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    payload += b" " * (-len(payload) % 4)
    pieces = [struct.pack("<II", len(payload), 0x4E4F534A), payload]
    for kind, data in chunks:
        pieces.extend([struct.pack("<II", len(data), kind), data])
    body = b"".join(pieces)
    return struct.pack("<4sII", b"glTF", 2, len(body) + 12) + body


def export_facial(pf_path, glb_path, output):
    pf_path, glb_path, output = Path(pf_path), Path(glb_path), Path(output)
    if any(not path.is_absolute() for path in (pf_path, glb_path, output)):
        raise ValueError("both input paths and the new output directory must be absolute")
    pf_path, glb_path = pf_path.resolve(strict=True), glb_path.resolve(strict=True)
    if output.exists():
        raise FileExistsError(f"output directory already exists: {output}")
    if pf_path.stat().st_size > 128 * 1024 * 1024 or glb_path.stat().st_size > MAX_GLB_BYTES:
        raise ValueError("an input exceeds the byte budget")
    pf_data, glb_data = pf_path.read_bytes(), glb_path.read_bytes()
    package = parse_pf_package(pf_data)
    document, chunks = read_glb(glb_data)
    original_document = copy.deepcopy(document)
    runtime = document.get("extras", {}).get("reaPicaRuntime")
    if not isinstance(runtime, dict) or runtime.get("schema") != "rea.pica.runtime.v1":
        raise ValueError("GLB lacks a supported REA PICA runtime")
    if "facial_animation" in runtime:
        raise ValueError("GLB already has a facial animation contract")
    facial = make_facial_runtime(package, runtime.get("source_model"))
    runtime["facial_animation"] = facial
    new_glb = write_glb(document, chunks)
    decoded, new_chunks = read_glb(new_glb)
    restored_document = copy.deepcopy(decoded)
    del restored_document["extras"]["reaPicaRuntime"]["facial_animation"]
    if restored_document != original_document or chunks != new_chunks:
        raise ValueError("the isolated GLB bridge changed an existing source field or binary chunk")
    expectations = {"schema": "rea3d-pf-facial-expectations-v1", "source_sha256": package["source_sha256"],
                    "game_executed": False, "original_game_parity_verified": False, "samples": []}
    for group in facial["groups"]:
        for face_index in [-1] + group["valid_source_face_indices"]:
            expectations["samples"].append(sample_facial_runtime(package, group["channel"], face_index))
    unchanged = (pf_path.read_bytes() == pf_data and glb_path.read_bytes() == glb_data)
    if not unchanged:
        raise RuntimeError("an input changed while the bridge was running")
    receipt = {"schema": "rea3d-pf-facial-export-receipt-v1", "game_executed": False,
               "original_game_parity_verified": False, "source_inputs_unchanged": unchanged,
               "pf": {"path": str(pf_path), "sha256": package["source_sha256"],
                      "bytes": len(pf_data), "decompressed_bytes": package["decompressed_bytes"]},
               "base_glb": {"path": str(glb_path), "sha256": hashlib.sha256(glb_data).hexdigest()},
               "output_glb": {"path": str(output / "model.glb"), "sha256": hashlib.sha256(new_glb).hexdigest(),
                              "bytes": len(new_glb)},
               "only_new_document_field": "extras.reaPicaRuntime.facial_animation",
               "existing_document_fields_preserved": True, "non_json_chunks_preserved_exactly": True,
               "groups": [{"channel": g["channel"], "source_entries": g["source_entries"],
                           "source_end_frame": g["source_end_frame"]} for g in facial["groups"]],
               "expectation_samples": len(expectations["samples"])}
    output.mkdir(parents=True, exist_ok=False)
    (output / "model.glb").write_bytes(new_glb)
    for filename, value in [("facial-runtime.json", facial), ("pica-runtime.json", runtime),
                            ("pf-package.json", serialize(package)), ("expectations.json", expectations),
                            ("export-receipt.json", receipt)]:
        with (output / filename).open("x", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False, indent=2)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pf", required=True)
    parser.add_argument("--glb", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(export_facial(args.pf, args.glb, args.output), ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
