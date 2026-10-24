"""Export source-bound integer-frame expectations for an independent JS evaluator.

SPDX-License-Identifier: GPL-3.0-or-later
Only the explicitly named PB/PK files are read. Output is created exclusively.
No game or GUI process is launched and no core or vendored reader is modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT / "python") not in sys.path:
    sys.path.insert(0, str(PROJECT / "python"))

from rea3d_core.bch_animation import parse_pokemon_pack, PRIMITIVES, TARGETS


def export_expectations(paths, output):
    output = Path(output)
    if not output.is_absolute():
        raise ValueError("output must be an absolute JSON path")
    if output.exists():
        raise FileExistsError(f"expectations output already exists: {output}")
    report = {
        "schema": "rea3d-visual-curve-expectations-v1",
        "execution": "python_source_curve_integer_frame_evaluation",
        "sample_rule": "Every integer source frame from 0 through floor(animation.end_frame), inclusive; no animation looping/remapping is applied.",
        "game_executed": False,
        "original_game_parity_verified": False,
        "axis_rule": "curve_index preserves original vector channel position: 0=X/R, 1=Y/G, 2=Z/B, 3=W/A. Missing channels are null, never shifted.",
        "packages": [],
        "totals": {"animations": 0, "curve_descriptors": 0, "present_curves": 0,
                   "missing_curves": 0, "expected_values": 0, "numeric_values": 0, "boolean_values": 0},
    }
    for source in paths:
        source = Path(source).resolve(strict=True)
        data = source.read_bytes()
        pack = parse_pokemon_pack(data, source.name)
        if pack["errors"]:
            raise ValueError(f"{source.name}: parsing errors prevent complete expectations: {pack['errors']}")
        package = {"pack": pack["kind"], "source_path": str(source), "source_sha256": hashlib.sha256(data).hexdigest(),
                   "source_bytes": len(data), "record_count": pack["record_count"],
                   "warnings": pack["warnings"], "animations": [], "curves": []}
        for entry, parsed in sorted(pack["entries"].items(), key=lambda item: int(item[0])):
            for kind in ("material", "visibility"):
                for animation_index, animation in enumerate(parsed[kind]):
                    count = math.floor(animation.end_frame)+1
                    if not 1 <= count <= 100001:
                        raise ValueError("animation sample range exceeds the expected bound")
                    package["animations"].append({"entry": int(entry), "animation_index": animation_index,
                                                   "animation": animation.name, "kind": kind,
                                                   "end_frame": animation.end_frame, "element_count": len(animation.elements)})
                    report["totals"]["animations"] += 1
                    for element_index, element in enumerate(animation.elements):
                        for curve_index, curve in enumerate(element.curves):
                            present = curve is not None
                            values = [curve.sample(frame) if present else None for frame in range(count)]
                            if any(isinstance(value, float) and not math.isfinite(value) for value in values):
                                raise ValueError("curve evaluation produced a non-finite number")
                            package["curves"].append({
                                "pack": pack["kind"], "entry": int(entry), "animation_index": animation_index,
                                "animation": animation.name, "animation_kind": kind, "animation_flags": animation.flags,
                                "end_frame": animation.end_frame, "element_index": element_index, "name": element.name,
                                "target_id": element.target_id, "target": TARGETS[element.target_id],
                                "primitive_id": element.primitive_id, "primitive": PRIMITIVES[element.primitive_id],
                                "curve_index": curve_index, "present": present,
                                "curve": curve.to_dict() if present else None, "first_frame": 0,
                                "samples": values,
                            })
                            totals = report["totals"]
                            totals["curve_descriptors"] += 1
                            totals["present_curves" if present else "missing_curves"] += 1
                            totals["expected_values"] += len(values)
                            totals["numeric_values"] += sum(isinstance(v, (float, int)) and not isinstance(v, bool) for v in values)
                            totals["boolean_values"] += sum(isinstance(v, bool) for v in values)
        report["packages"].append(package)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        handle.write("\n")
    return {"path": str(output), "bytes": output.stat().st_size,
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "totals": report["totals"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pb", type=Path, default=Path("D:/REA3D-ORAS-20261023-1792751073625/members-first20/00008_00.bin"))
    parser.add_argument("--pk", type=Path, default=Path("D:/REA3D-ORAS-20261023-1792751073625/members-first20/00009_00.bin"))
    parser.add_argument("--output", type=Path, default=Path("D:/REA3D-ORAS-MATERIAL-20261023-02/visual-curve-expectations.json"))
    args = parser.parse_args()
    result = export_expectations([args.pb, args.pk], args.output)
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
