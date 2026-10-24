"""PF range rejection, semantic identity gate and independent selected-file samples.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import importlib.util
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest

from rea3d_core.pf_facial import make_facial_runtime, parse_pf_package, sample_facial_runtime
from rea3d_core.bridge import AssetError, Assets, capabilities, dispatch, read_assets
from test_bch_animation import build_bch
from test_fixture import make_fixture


def pf(entries):
    offsets = [4 + 4 * (len(entries) + 1)]
    for entry in entries:
        offsets.append(offsets[-1] + len(entry))
    return b"PF" + struct.pack("<H", len(entries)) + struct.pack("<" + "I" * len(offsets), *offsets) + b"".join(entries)


class PFRangeTests(unittest.TestCase):
    def test_native_capability_declares_selected_facial_scope(self):
        cap = capabilities()
        self.assertIn("PF", cap["containers"])
        self.assertEqual(cap["pf_facial"]["input_option"], "motion_paths")
        self.assertFalse(cap["pf_facial"]["adds_standard_gltf_animation_clips"])
        self.assertFalse(cap["pf_facial"]["original_game_parity_verified"])

    def test_valid_empty_offsets_and_bch_dictionary_do_not_invent_semantics(self):
        package = parse_pf_package(pf([build_bch(), b"", b""]))
        self.assertEqual(package["entry_count"], 3)
        self.assertEqual(package["entries"][1]["length"], 0)
        self.assertEqual(len(package["entries"][0]["material"]), 1)
        self.assertEqual(len(package["entries"][0]["visibility"]), 1)
        self.assertFalse(package["selected_native_profile_verified"])
        with self.assertRaisesRegex(ValueError, "identified"):
            make_facial_runtime(package, "pm0001_00")
        with self.assertRaisesRegex(ValueError, "identified"):
            sample_facial_runtime(package, 0, 1)

    def test_rejects_short_wrong_count_overlapping_table_and_descending_ranges(self):
        invalid = [b"PF", b"XX\x01\0\x0c\0\0\0\x0c\0\0\0",
                   b"PF\0\0" + bytes(4), b"PF\x02\0" + struct.pack("<III", 12, 16, 16),
                   b"PF\x02\0" + struct.pack("<III", 20, 16, 20) + bytes(4),
                   b"PF\x01\0" + struct.pack("<II", 12, 24)]
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValueError):
                parse_pf_package(data)

    def test_rejects_declared_compression_bomb_before_decompression(self):
        with self.assertRaisesRegex(ValueError, "budget"):
            parse_pf_package(b"\x11\0\0\0" + struct.pack("<I", 129 * 1024 * 1024))
        with self.assertRaises(ValueError):
            parse_pf_package(b"\x11\0\0\0")

    def test_rejects_unsupported_nonempty_entry(self):
        with self.assertRaisesRegex(ValueError, "not supported BCH"):
            parse_pf_package(pf([b"an unknown entry"]))

    def test_rejects_invalid_facial_channel_and_value(self):
        for channel, value in [(-1, 0), (6, 0), (0, -2), (0, .5)]:
            with self.subTest(channel=channel, value=value), self.assertRaises(ValueError):
                sample_facial_runtime({}, channel, value)

    def test_native_export_rejects_empty_unknown_and_overbudget_before_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model, _ = make_fixture(root / "fixture")
            motion = root / "candidate.pf"
            cases = [(b"", "NO_SUPPORTED_MOTION"),
                     (pf([build_bch(False, True)]), "UNSUPPORTED_PF_PROFILE"),
                     (b"PF\x02\0" + struct.pack("<III", 20, 16, 20) + bytes(4), "INVALID_PF_PACKAGE"),
                     (b"\x11\0\0\0" + struct.pack("<I", 257 * 1024 * 1024), "LIMIT_EXCEEDED")]
            for index, (data, expected) in enumerate(cases):
                motion.write_bytes(data)
                output = root / f"out{index}"
                with self.subTest(expected=expected), self.assertRaises(AssetError) as caught:
                    dispatch("export", {"path": str(model), "motion_paths": [str(motion)], "output_dir": str(output)})
                self.assertEqual(caught.exception.code, expected)
                self.assertFalse(output.exists())

    def test_native_rejects_regular_file_over_input_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "oversized.pf"
            with source.open("wb") as handle:
                handle.write(b"PF")
                handle.truncate(128 * 1024 * 1024 + 1)
            with self.assertRaises(AssetError) as caught:
                read_assets(source, required_role="motion")
            self.assertEqual(caught.exception.code, "LIMIT_EXCEEDED")


class SelectedPFTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = os.environ.get("REA3D_PF_FIXTURE")
        if not fixture:
            raise unittest.SkipTest("REA3D_PF_FIXTURE is not set")
        cls.fixture = Path(fixture)
        cls.package = parse_pf_package(cls.fixture.read_bytes())

    def test_original_eye_samples_and_bch_empty_mouth_tracks(self):
        package = self.package
        self.assertTrue(package["selected_native_profile_verified"])
        runtime = make_facial_runtime(package, "pm0001_00")
        self.assertEqual([(g["channel"], g["source_entries"], g["source_end_frame"]) for g in runtime["groups"]],
                         [(0, [0], 80.), (3, [9, 10, 11], 70.)])
        for value, expected in [(0, 0), (1, .25), (2, .5), (3, .75), (4, 0)]:
            tracks = sample_facial_runtime(package, 0, value)["materials"]
            eye = next(t for t in tracks if t["name"] == "Eye" and t["target_id"] == 21)
            self.assertAlmostEqual(eye["values"][1], expected, places=6)
        self.assertEqual(sample_facial_runtime(package, 0, -1)["materials"], [])
        self.assertEqual(sample_facial_runtime(package, 3, 2)["materials"], [])
        self.assertEqual(sample_facial_runtime(package, 3, 2)["visibility"], [])
        self.assertEqual(sample_facial_runtime(package, 0, 99),
                         {**sample_facial_runtime(package, 0, 8), "face_index": 99})
        self.assertIsNone(runtime["control"]["automatic_blink"]["update_rate_hz"])
        with self.assertRaises(ValueError):
            make_facial_runtime(package, "pm0002_00")

    def test_native_motion_companion_preserves_compressed_source_hash_without_fake_clips(self):
        assets = read_assets(self.fixture, required_role="motion")
        self.assertEqual(assets.motions, [])
        self.assertEqual(len(assets.facial_motion_packages), 1)
        self.assertEqual(assets.facial_motion_packages[0]["source_sha256"], self.package["source_sha256"])
        self.assertEqual(assets.nodes[-1]["format"], "PF")
        inspected = dispatch("inspect", {"path": str(self.fixture)})
        self.assertEqual(inspected["animations"], [])
        self.assertEqual(inspected["facial_motion_packages"][0]["source_model"], "pm0001_00")

    def test_native_rejects_duplicate_packages_and_wrong_model_before_writing(self):
        assets = read_assets(self.fixture, required_role="motion")
        with self.assertRaises(AssetError) as duplicate:
            read_assets(self.fixture, assets, required_role="motion")
        self.assertEqual(duplicate.exception.code, "MULTIPLE_FACIAL_PACKAGES")
        with tempfile.TemporaryDirectory() as directory:
            model, _ = make_fixture(Path(directory) / "fixture")
            output = Path(directory) / "wrong-model"
            with self.assertRaises(AssetError) as wrong:
                dispatch("export", {"path": str(model), "motion_paths": [str(self.fixture)], "output_dir": str(output)})
            self.assertEqual(wrong.exception.code, "PF_MODEL_INCOMPATIBLE")
            self.assertFalse(output.exists())

    def test_native_runtime_rejects_different_species_even_with_pica_config(self):
        from types import SimpleNamespace
        from rea3d_core.runtime_export import make_runtime
        assets = Assets(facial_motion_packages=[self.package])
        for name, config in [("pm0002_00", {"materials": []}), ("pm0001_00", None)]:
            model = SimpleNamespace(name=name, pica_config=config)
            with self.subTest(name=name, config=config), self.assertRaises(AssetError) as caught:
                make_runtime(None, model, assets, [], 30)
            self.assertEqual(caught.exception.code, "PF_MODEL_INCOMPATIBLE")

    def test_native_real_model_embeds_facial_contract_and_preserves_primary_clip_count(self):
        parent = self.fixture.parent
        model, texture, pb, pk = [parent / name for name in ("00003_00.bin", "00005_00.bin", "00008_00.bin", "00009_00.bin")]
        if not all(path.is_file() for path in (model, texture, pb, pk)):
            self.skipTest("selected companion model/PB/PK/texture fixtures are unavailable")
        from test_core import read_glb
        with tempfile.TemporaryDirectory() as directory:
            result = dispatch("export", {"path": str(model), "motion_paths": [str(pb), str(pk), str(self.fixture)],
                                         "texture_paths": [str(texture)], "output_dir": str(Path(directory) / "export")})
            document, _ = read_glb(result["glb_path"])
            self.assertEqual(result["animation_count"], 30)
            self.assertEqual(result["auxiliary_pose_clip_count"], 2)
            runtime = document["extras"]["reaPicaRuntime"]
            self.assertEqual([group["channel"] for group in runtime["facial_animation"]["groups"]], [0, 3])
            self.assertEqual(runtime["facial_animation"]["source_sha256"], self.package["source_sha256"])
            self.assertEqual(result["requested_motion_file_count"], 3)
            self.assertFalse(result["original_rendering_verified"])


class GLBBridgeTests(unittest.TestCase):
    def test_roundtrip_preserves_unknown_chunks_and_rejects_truncation(self):
        path = Path(__file__).resolve().parents[1] / "scripts" / "export-pf-facial.py"
        spec = importlib.util.spec_from_file_location("export_pf_facial", path)
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
        document = {"asset": {"version": "2.0"}, "extras": {"unchanged": [1, 2, "한글"]}}
        chunks = [(0x004E4942, b"\0\1\2\3"), (0xAABBCCDD, b"ABCD")]
        blob = bridge.write_glb(document, chunks)
        self.assertEqual(bridge.read_glb(blob), (document, chunks))
        for invalid in [blob[:-1], blob[:11], blob + b"ABCD"]:
            with self.assertRaises(ValueError):
                bridge.read_glb(invalid)


if __name__ == "__main__":
    unittest.main()
