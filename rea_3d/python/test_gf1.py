"""Original GF1 binary fixtures and regression tests; no game assets.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import math
import os
from pathlib import Path
import struct
import tempfile
import unittest

from rea3d_core.bridge import AssetError, dispatch, read_assets
from rea3d_core.gf1 import ORAS_NATIVE_CODE_SHA256, read_pack
from test_core import read_glb, accessor
from test_fixture import make_fixture, pc_bytes


def gf1_pack(curve=6, flags=0, bad_clip=False):
    count = 3 if bad_clip else 2
    data = bytearray(4+4*count)
    skeleton_offset = len(data)
    data += bytes([2, 0, 0, flags, 0])+b"Root\0"
    data += bytes((-len(data)) % 4)
    data += struct.pack("<7f", 0, 0, 0, 0, 0, 0, 1)*2
    motion_offset = len(data)
    octals = [0, 0, 1, 1, 1, curve, 0, 0, 1, 1]
    data += struct.pack("<HH", len(octals), 2)
    for i in range(0, len(octals), 8):
        word = sum(opcode << (3*j) for j, opcode in enumerate(octals[i:i+8]))
        data += word.to_bytes(3, "little")
    if curve >= 6:
        data += bytes([1, 1])  # One interior key at frame 1; implicit endpoints 0,2.
    data += bytes((-len(data)) % 4)
    if curve == 5:
        data += struct.pack("<f", 2)
    elif curve == 6:
        data += struct.pack("<3f", 0, 4, 8)
    elif curve == 7:
        data += struct.pack("<6f", 0, 120, 4, 120, 8, 120)
    offsets = [skeleton_offset, motion_offset]
    if bad_clip:
        offsets.append(len(data))
        data += b"\xff"
    struct.pack_into("<I", data, 0, count)
    struct.pack_into("<"+"I"*count, data, 4, *offsets)
    return bytes(data)


def gf_package(magic, *entries):
    return magic+pc_bytes(*entries)[2:]


class GF1Tests(unittest.TestCase):
    def test_bounded_pack_reads_names_and_linear_values(self):
        result = read_pack(gf1_pack())
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["warnings"], [])
        motion = result["motions"][0]
        self.assertEqual(motion.frame_count, 3)
        self.assertEqual([b.name for b in motion.bones], ["Root"])
        curve = motion.bones[0].translation[0]
        self.assertEqual(curve.sample(.5), 2.)
        self.assertEqual(curve.sample(1.5), 6.)

    def test_hermite_slope_scaling_is_explicit_and_values_are_checked(self):
        result = read_pack(gf1_pack(curve=7))
        curve = result["motions"][0].bones[0].translation[0]
        self.assertEqual(curve.keys[0].slope, 4.)
        self.assertAlmostEqual(curve.sample(.5), 2.)
        self.assertIn("GF1_SLOPE_SCALE_ASSUMPTION", [w["code"] for w in result["warnings"]])

    def test_invalid_clip_is_separated_from_successful_clip(self):
        result = read_pack(gf1_pack(bad_clip=True))
        self.assertEqual(len(result["motions"]), 1)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["errors"][0]["index"], 1)

    def test_world_space_flag_is_reported_and_marked(self):
        result = read_pack(gf1_pack(flags=8))
        self.assertTrue(result["motions"][0].bones[0].gf1_world_space)
        self.assertIn("GF1_WORLD_SPACE_UNSUPPORTED", [w["code"] for w in result["warnings"]])

    def test_all_world_space_clip_is_not_exported_as_local_motion(self):
        with tempfile.TemporaryDirectory() as directory:
            model, motion = make_fixture(directory)
            motion.write_bytes(gf_package(b"PB", gf1_pack(flags=8)))
            output = Path(directory)/"out"
            with self.assertRaises(AssetError) as context:
                dispatch("export", {"path": str(model), "motion_paths": [str(motion)], "output_dir": str(output)})
            self.assertEqual(context.exception.code, "NO_TRACKS_BOUND")
            self.assertFalse(output.exists())

    def test_bad_offsets_do_not_trigger_speculative_parsing(self):
        data = bytearray(gf1_pack())
        struct.pack_into("<I", data, 4, 0xFFFFFFFF)
        with self.assertRaises(ValueError):
            read_pack(bytes(data))

    def test_pb_and_pk_binary_to_glb_motion_values(self):
        for magic in (b"PB", b"PK"):
            with self.subTest(magic=magic), tempfile.TemporaryDirectory() as directory:
                model, motion = make_fixture(directory)
                motion.write_bytes(gf_package(magic, gf1_pack()))
                assets = read_assets(motion)
                self.assertEqual(assets.nodes[0]["format"], "GF1MotionPack")
                result = dispatch("export", {"path": str(model), "motion_paths": [str(motion)],
                                             "output_dir": str(Path(directory)/"out")})
                # This skeletal fixture omits the real PB/PK visual-animation
                # tables. Preserve the valid motion while reporting that gap.
                self.assertEqual(result["status"], "partial")
                self.assertIn("VISUAL_PACKAGE_PARSE_FAILED", [w["code"] for w in result["warnings"]])
                doc, binary = read_glb(result["glb_path"])
                animation = doc["animations"][0]
                channel = next(c for c in animation["channels"] if c["target"]["path"] == "translation")
                values = accessor(doc, binary, animation["samplers"][channel["sampler"]]["output"])
                self.assertEqual(values, [(0.,0.,0.), (4.,0.,0.), (8.,0.,0.)])

    def test_partial_gf1_pack_does_not_claim_complete_animation(self):
        with tempfile.TemporaryDirectory() as directory:
            model, motion = make_fixture(directory)
            motion.write_bytes(gf_package(b"PB", gf1_pack(bad_clip=True)))
            result = dispatch("export", {"path": str(model), "motion_paths": [str(motion)],
                                         "output_dir": str(Path(directory)/"out")})
            self.assertEqual(result["status"], "partial")
            self.assertFalse(result["animation_export_complete"])
            self.assertIn("GF1_MOTION_PARSE_FAILED", [w["code"] for w in result["warnings"]])


class SelectedNativeGF1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = os.environ.get("REA3D_PF_FIXTURE")
        if not fixture:
            raise unittest.SkipTest("REA3D_PF_FIXTURE is not set for selected ORAS companions")
        from ctr.compress.lz import decompress_lz
        cls.raw = {}
        for kind, filename in [("PB", "00008_00.bin"), ("PK", "00009_00.bin")]:
            source = Path(fixture).parent / filename
            if not source.is_file():
                raise unittest.SkipTest("selected ORAS GF1 companions are unavailable")
            package = decompress_lz(source.read_bytes())
            start, end = struct.unpack_from("<II", package, 4)
            cls.raw[kind] = package[start:end]

    def test_selected_native_profile_carries_original_code_and_slope_unit_evidence(self):
        for kind, data in self.raw.items():
            with self.subTest(kind=kind):
                pack = read_pack(data)
                profile = pack["native_interpolation_profile"]
                self.assertEqual(profile["package"], kind)
                self.assertEqual(profile["source_code_sha256"], ORAS_NATIVE_CODE_SHA256)
                self.assertEqual(profile["native_slope_time_scale_hexword"], "0x3d088889")
                self.assertTrue(profile["slope_unit_scale_verified_from_original_code"])
                self.assertFalse(profile["native_code_executed_by_parser"])
                self.assertFalse(profile["original_game_visual_parity_verified"])
                self.assertNotIn("GF1_SLOPE_SCALE_ASSUMPTION", [warning["code"] for warning in pack["warnings"]])

    def test_original_opcode6_witness_disagrees_with_linear_and_matches_native(self):
        pack = read_pack(self.raw["PB"])
        motion = next(item for item in pack["motions"] if item.index == 1)
        curve = next(bone for bone in motion.bones if bone.name == "LShoulder").translation[0]
        self.assertEqual(curve.interpolation, "smoothstep")
        # Expected result comes from the separately reconstructed original VFP
        # sequence at0x54ec58..0x54ec9c, using this original raw curve atframe19.
        self.assertAlmostEqual(curve.sample(19), 10.144573211669922, delta=2e-6)
        self.assertGreater(abs(curve.sample(19) - 9.76923360824585), .37)

    def test_unknown_identity_retains_reference_behavior_and_warning(self):
        unknown = read_pack(self.raw["PB"] + bytes(4))
        self.assertNotIn("native_interpolation_profile", unknown)
        motion = next(item for item in unknown["motions"] if item.index == 1)
        curve = next(bone for bone in motion.bones if bone.name == "LShoulder").translation[0]
        self.assertEqual(curve.interpolation, "linear")
        self.assertAlmostEqual(curve.sample(19), 9.76923360824585, places=12)
        self.assertIn("GF1_SLOPE_SCALE_ASSUMPTION", [warning["code"] for warning in unknown["warnings"]])

    def test_selected_idle_and_discrete_auxiliary_poses_preserve_prior_values(self):
        for kind, data in self.raw.items():
            current, prior = read_pack(data), read_pack(data + bytes(4))
            current_idle = next(motion for motion in current["motions"] if motion.index == 0)
            prior_idle = next(motion for motion in prior["motions"] if motion.index == 0)
            for current_bone, prior_bone in zip(current_idle.bones, prior_idle.bones):
                self.assertEqual(current_bone.name, prior_bone.name)
                for lhs, rhs in zip(current_bone.translation + current_bone.rotation + current_bone.scale,
                                    prior_bone.translation + prior_bone.rotation + prior_bone.scale):
                    for frame in range(current_idle.frame_count):
                        self.assertEqual(lhs.sample(frame), rhs.sample(frame))
            auxiliary_slot = 25 if kind == "PB" else 33
            current_aux = next(motion for motion in current["motions"] if motion.index == auxiliary_slot)
            prior_aux = next(motion for motion in prior["motions"] if motion.index == auxiliary_slot)
            for current_bone, prior_bone in zip(current_aux.bones, prior_aux.bones):
                for lhs, rhs in zip(current_bone.rotation, prior_bone.rotation):
                    for face_index in range(8):
                        self.assertEqual(lhs.sample(face_index * 10), rhs.sample(face_index * 10))


if __name__ == "__main__":
    unittest.main()
