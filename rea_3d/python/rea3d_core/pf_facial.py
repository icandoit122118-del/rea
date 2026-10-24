"""Bounded PF facial BCH reader and selected ORAS native channel contract.

SPDX-License-Identifier: GPL-3.0-or-later
PF entry lookup is corroborated by selected shipped ARM code at 0x472e30,
0x12fb08 and 0x12fb40. Facial application is at 0x4717a8/0x475a7c.
The semantic profile is identity gated; arbitrary PF files are only parsed.
"""
from __future__ import annotations

import hashlib
import math
import struct

from ctr.compress.lz import decompress_lz, sniff_lz
from .bch_animation import MAX_BYTES, MAX_ELEMENTS, evaluate_animation, parse_bch, serialize

SELECTED_PF_SHA256 = "c98e1b566ea10745e6374db5348f231c9c92ceabf54233572e50f4841dc7246c"
SELECTED_DECOMPRESSED_SHA256 = "559319daa6b8e9a3260ca1ca5fa8cc776fa35354cd1a0a0bdab74f2ead6f011e"
SELECTED_CODE_SHA256 = "d587c98ac5c4dedacf9be4baf2d6b7d10169e6a63f8bc35b132002cc27bc1a37"
BLINK_VALUES = (1, 1, 2, 2, 1, 1, 0, 0, 0)


def parse_pf_package(source):
    """Parse explicit offset ranges without guessing a facial channel for unknown files."""
    if not isinstance(source, (bytes, bytearray)) or len(source) > MAX_BYTES:
        raise ValueError("PF input must be bytes inside the byte budget")
    blob = bytes(source)
    source_sha = hashlib.sha256(blob).hexdigest()
    compressed = sniff_lz(blob[:8])
    if compressed:
        declared = int.from_bytes(blob[1:4], "little")
        if not declared:
            if len(blob) < 8:
                raise ValueError("PF extended compression size is truncated")
            declared = int.from_bytes(blob[4:8], "little")
        if not 4 <= declared <= MAX_BYTES:
            raise ValueError("PF decompressed size exceeds the byte budget")
        blob = decompress_lz(blob)
        if len(blob) != declared:
            raise ValueError("PF decompressed size differs from its header")
    if len(blob) < 8 or blob[:2] != b"PF":
        raise ValueError("expected a PF package")
    count, = struct.unpack_from("<H", blob, 2)
    table_end = 4 + 4 * (count + 1)
    if not 1 <= count <= MAX_ELEMENTS or table_end > len(blob):
        raise ValueError("PF entry table exceeds its count or byte budget")
    offsets = struct.unpack_from("<" + "I" * (count + 1), blob, 4)
    if (offsets[0] < table_end or offsets[-1] > len(blob)
            or any(a > b for a, b in zip(offsets, offsets[1:]))):
        raise ValueError("PF offset ranges are invalid")
    entries = []
    for index, (start, end) in enumerate(zip(offsets, offsets[1:])):
        entry = {"entry": index, "offset": start, "length": end - start,
                 "material": [], "visibility": []}
        if start != end:
            payload = blob[start:end]
            if payload[:4] != b"BCH\0":
                raise ValueError(f"PF entry {index} is nonempty and is not supported BCH")
            parsed = parse_bch(payload)
            errors = list(parsed["errors"])
            for kind in ("material", "visibility"):
                for animation in parsed[kind]:
                    errors.extend(animation.errors)
                    entry[kind].append(animation)
            if errors:
                raise ValueError(f"PF entry {index} has BCH errors: {errors}")
        entries.append(entry)
    digest = hashlib.sha256(blob).hexdigest()
    return {"schema": "rea3d-pf-package-v1", "source_sha256": source_sha,
            "source_bytes": len(source), "compressed": compressed,
            "decompressed_sha256": digest, "decompressed_bytes": len(blob),
            "entry_count": count, "entries": entries,
            "selected_native_profile_verified": digest == SELECTED_DECOMPRESSED_SHA256}


def make_facial_runtime(package, source_model):
    """Expose the native 6-channel profile only for the identified Bulbasaur PF."""
    if not package.get("selected_native_profile_verified") or source_model != "pm0001_00":
        raise ValueError("facial channel semantics require the identified ORAS Bulbasaur PF and model")
    entries = package["entries"]
    groups = []
    for channel in range(6):
        indices = [channel * 3 + index for index in range(3)]
        material = entries[indices[0]]["material"] + entries[indices[2]]["material"]
        visibility = entries[indices[1]]["visibility"]
        if (entries[indices[0]]["visibility"] or entries[indices[2]]["visibility"]
                or entries[indices[1]]["material"]):
            raise ValueError("selected PF dictionary kinds disagree with the native loader contract")
        animations = material + visibility
        if not animations:
            continue
        end_frames = {animation.end_frame for animation in animations}
        if len(end_frames) != 1:
            raise ValueError("selected PF group has differing source end frames")
        end, = end_frames
        groups.append({"channel": channel,
                       "source_entries": [i for i in indices if entries[i]["length"]],
                       "source_end_frame": end, "frame_multiplier": 10,
                       "material_animations": material, "visibility_animations": visibility,
                       "source_scene_indices": [channel + 3, channel + 9, channel + 15],
                       "valid_source_face_indices": list(range(math.floor(end / 10) + 1))})
    return serialize({
        "schema": "rea3d-pf-facial-runtime-v1", "source_model": source_model,
        "source_package": "PF", "source_sha256": package["source_sha256"],
        "decompressed_sha256": package["decompressed_sha256"],
        "native_code_sha256": SELECTED_CODE_SHA256,
        "native_loader": "0x472e30", "native_frame_setter": "0x475a7c",
        "channel_contract_basis": "original ARM entry lookup and controller routing, checked against BCH dictionary kinds",
        "groups": groups,
        "control": {"detach_value": -1, "frame_multiplier": 10,
                    "source_frame_rule": "min(float32(face_index) * 10.0f, animation.end_frame)",
                    "evaluation_order": "primary material/visibility, automatic blink state update, facial material/visibility override",
                    "primary_clock_independent": True,
                    "face_6_7_mask": {"native_object_offset": "0x44e", "first_group_bits": [1, 2],
                                      "second_group_bits": [4, 8], "masked_result": 0,
                                      "runtime_value_observed": False},
                    "automatic_blink": {"native_update": "0x470ff8", "values": list(BLINK_VALUES),
                                        "source_frames": [10 * value for value in BLINK_VALUES],
                                        "channels": [0, 1, 2], "wait_min_updates": 240,
                                        "wait_random_upper_exclusive": 240,
                                        "update_rate_hz": None,
                                        "gates": ["blink enabled at object+0x32c",
                                                  "current native skeletal motion ID low byte equals 2",
                                                  "active update flag object+0x339 decrements wait",
                                                  "object+0x3f5 changes wait/sequence step from 1 to 2",
                                                  "special state read via object+0x448 affects accelerated sequence advancement"],
                                        "default_playback": "explicit supplied state or replay only; no invented wall-clock RNG schedule"}},
        "game_executed": False, "dynamic_trigger_verified": False,
        "original_game_pixel_parity_verified": False,
        "limitations": ["Native branch and asset contracts are static evidence.",
                        "Runtime object mask, RNG state, update cadence and exact game event values require original-game observation.",
                        "Input range offered by a player is a bounded source sample range; source presence does not prove a game event selected every value."]})


def sample_facial_runtime(package, channel, face_index):
    """Independent Python expectations, with -1 meaning no overriding tracks."""
    if not isinstance(channel, int) or not 0 <= channel < 6:
        raise ValueError("facial channel must be an integer in 0..5")
    if not isinstance(face_index, int) or face_index < -1:
        raise ValueError("face index must be -1 or a nonnegative integer")
    if not package.get("selected_native_profile_verified"):
        raise ValueError("sample semantics require the identified native profile")
    result = {"channel": channel, "face_index": face_index, "materials": [], "visibility": []}
    if face_index == -1:
        return result
    for index, kind in [(3 * channel, "material"), (3 * channel + 1, "visibility"),
                        (3 * channel + 2, "material")]:
        for animation in package["entries"][index][kind]:
            frame = min(face_index * 10., animation.end_frame)
            result["materials" if kind == "material" else "visibility"].extend(
                evaluate_animation(animation, frame, loop=False))
    return result
