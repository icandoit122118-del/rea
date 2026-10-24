"""Contract and binary-to-GLB tests. SPDX-License-Identifier: GPL-3.0-or-later."""
import json
import math
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

from rea3d_core.bridge import AssetError, capabilities, dispatch, read_assets
from rea3d_core.glb import local_matrix, multiply, inverse
from test_fixture import make_fixture, model_bytes, motion_bytes, pc_bytes, lz10_literal_bytes, texture_bytes, sparse_axis_angle_motion_bytes


def read_glb(path):
    data = Path(path).read_bytes()
    magic, version, total = struct.unpack_from("<III", data)
    if (magic, version, total) != (0x46546C67, 2, len(data)):
        raise AssertionError("invalid GLB header")
    json_length, json_kind = struct.unpack_from("<II", data, 12)
    if json_kind != 0x4E4F534A:
        raise AssertionError("missing JSON chunk")
    doc = json.loads(data[20:20+json_length])
    at = 20+json_length
    bin_length, bin_kind = struct.unpack_from("<II", data, at)
    if bin_kind != 0x004E4942 or at+8+bin_length != len(data):
        raise AssertionError("invalid BIN chunk")
    return doc, data[at+8:]


def accessor(doc, binary, index):
    acc = doc["accessors"][index]
    view = doc["bufferViews"][acc["bufferView"]]
    start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    widths = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
    fmt = {5126: "f", 5123: "H", 5125: "I"}[acc["componentType"]]
    row = struct.Struct("<" + fmt*widths[acc["type"]])
    return [row.unpack_from(binary, start+i*row.size) for i in range(acc["count"])]


class CoreTests(unittest.TestCase):
    def test_bch_uv_conversion_is_format_scoped_and_does_not_clamp_or_invent_uvs(self):
        from rea3d_core.glb import Document, _meshes
        from ctr.ir import Model, SubMesh
        source_uv = [(-.2,1.2),(.5,.25),(1.,0.)]
        for bch in [False, True]:
            with self.subTest(bch=bch):
                sub = SubMesh(name="triangle", positions=[(0,0,0),(1,0,0),(0,1,0)],
                              triangles=[(0,1,2)],uv0=source_uv,uv1=[],uv2=source_uv)
                if bch:
                    sub.bch_flip_v = True
                document = Document()
                _meshes(document, Model(submeshes=[sub]), {}, [])
                primitive = document.doc["meshes"][0]["primitives"][0]
                self.assertNotIn("TEXCOORD_1", primitive["attributes"])
                for semantic in ["TEXCOORD_0", "TEXCOORD_2"]:
                    actual = accessor(document.doc, document.blob, primitive["attributes"][semantic])
                    expected = [(u,1-v) for u,v in source_uv] if bch else source_uv
                    for actual_row, expected_row in zip(actual, expected):
                        for actual_value, expected_value in zip(actual_row, expected_row):
                            self.assertAlmostEqual(actual_value, expected_value, places=6)
                self.assertEqual(sub.uv0, source_uv)
                self.assertEqual(sub.uv2, source_uv)

    def test_pica_uniforms_decode_wzyx_and_fixed_float24(self):
        from rea3d_core.bridge import _bch_command_state
        from ctr.pica.attributes import AttributeName
        def command(register, values):
            return [values[0], register | (15 << 16) | ((len(values)-1) << 20)]+values[1:]+([0] if (len(values)+1) % 2 else [])
        words = command(0x2c0, [0x80000007])
        values = [struct.unpack("<I", struct.pack("<f", value))[0] for value in [4.,3.,2.,1.]]
        words += command(0x2c1, values)
        # Float24 encoded X=1,Y=2,Z=3,W=.5; fixed Color is attribute slot zero.
        x, y, z, w = 0x3f0000, 0x400000, 0x408000, 0x3e0000
        packed = [(w << 8) | (z >> 16), ((z & 0xffff) << 16) | (y >> 8), ((y & 255) << 24) | x]
        words += command(0x202, [1 << 16])+command(0x242, [0])+command(0x2bb, [3])
        words += command(0x232, [0])
        for register, value in zip([0x233,0x234,0x235], packed):
            words += command(register, [value])
        uniforms, fixed = _bch_command_state(words)
        self.assertEqual(uniforms[7], [1.,2.,3.,4.])
        self.assertEqual(fixed[AttributeName.Color], (1.,2.,3.,.5))
        words += command(0x2c0, [6])+command(0x2c1, packed)
        uniforms, _ = _bch_command_state(words)
        self.assertEqual(uniforms[6], [1.,2.,3.,.5])

    def test_bch_rigid_vertices_use_slots_and_bind_world_but_smooth_do_not(self):
        from rea3d_core.bridge import _adapt_bch_geometry
        from ctr.formats.bch.mesh import Mesh, SubMesh
        from ctr.formats.bch.material import Material
        from ctr.pica.attributes import Attribute, AttributeName, AttributeFormat
        from ctr.ir import Model
        raw_buffer = b"".join(struct.pack("<6fB", 1,0,0,1,0,0,slot)+bytes(3) for slot in [0,1,0])
        mesh = Mesh(material_index=0, vertex_stride=28, raw_buffer=raw_buffer,
                    attributes=[Attribute(AttributeName.Position, AttributeFormat.Float,3,1.),
                                Attribute(AttributeName.Normal, AttributeFormat.Float,3,1.),
                                Attribute(AttributeName.BoneIndex, AttributeFormat.Ubyte,1,1.)],
                    submeshes=[SubMesh(bone_indices=[1,0], indices=[0,1,2])])
        raw = SimpleNamespace(meshes_at=0, materials=[Material(name="test")], bones=[
            SimpleNamespace(parent=-1, scale=(1,1,1),rotation=(0,0,0),translation=(10,0,0)),
            SimpleNamespace(parent=0, scale=(1,1,1),rotation=(0,0,math.pi/2),translation=(0,5,0))])
        data = bytearray(256)
        struct.pack_into("<II",data,8,64,0)
        struct.pack_into("<II",data,16,128,1)
        data[128] = 2
        model = Model()
        with mock.patch("rea3d_core.bridge.read_meshes", return_value=[mesh]):
            _adapt_bch_geometry(data, raw, model)
        sub = model.submeshes[0]
        self.assertAlmostEqual(sub.positions[0][0],10.)
        self.assertAlmostEqual(sub.positions[0][1],6.)
        self.assertEqual(sub.positions[1],(11.,0.,0.))
        self.assertEqual(sub.bones,[[1],[0],[1]])
        self.assertAlmostEqual(sub.normals[0][1],1.)
        data[128] = 1
        with mock.patch("rea3d_core.bridge.read_meshes", return_value=[mesh]):
            _adapt_bch_geometry(data, raw, model)
        self.assertEqual(model.submeshes[0].positions,[(1.,0.,0.)]*3)
        self.assertEqual(model.submeshes[0].bones,[[1],[0],[1]])

    def test_bch_v21_material_layout_preserves_all_texture_names(self):
        from rea3d_core.bridge import _bch_materials_v21
        data = bytearray(512)
        at, cursor = 16, 128
        wanted = ["BodyA1", "BodyA2", "BodyANor", "BodyA"]
        pointers = []
        for name in wanted:
            pointers.append(cursor)
            encoded = name.encode()+b"\0"
            data[cursor:cursor+len(encoded)] = encoded
            cursor += len(encoded)
        struct.pack_into("<4I", data, at+0x1c, *pointers)
        material = _bch_materials_v21(data, at, 1)[0]
        self.assertEqual(material.name, "BodyA")
        self.assertEqual(material.textures, wanted[:3])
        with self.assertRaises(ValueError):
            _bch_materials_v21(data, len(data)-4, 1)

    def _export_sparse_axis_angle(self, directory, rest_z, rotation_x=None):
        model, motion = make_fixture(directory)
        model.write_bytes(pc_bytes(model_bytes(root_rotation=(0, 0, rest_z)), texture_bytes()))
        motion.write_bytes(pc_bytes(sparse_axis_angle_motion_bytes(rotation_x)))
        result = dispatch("export", {"path": str(model), "motion_paths": [str(motion)],
                                     "output_dir": str(Path(directory)/"out")})
        doc, binary = read_glb(result["glb_path"])
        channels = doc["animations"][0]["channels"]
        rotations = next(c for c in channels if c["target"]["path"] == "rotation")
        sampler = doc["animations"][0]["samplers"][rotations["sampler"]]
        return accessor(doc, binary, sampler["output"])

    def test_translation_only_axis_angle_motion_preserves_rest_euler_orientation(self):
        with tempfile.TemporaryDirectory() as directory:
            rotations = self._export_sparse_axis_angle(directory, .5)
            expected = (0., 0., math.sin(.25), math.cos(.25))
            for rotation in rotations:
                for actual, value in zip(rotation, expected):
                    self.assertAlmostEqual(actual, value, places=6)
                x, y, z, w = rotation
                # Independently rotate the Tip's rest offset (0,1,0) using q*v*q^-1.
                transformed_tip = (2*(x*y-z*w), 1-2*(x*x+z*z), 2*(y*z+x*w))
                self.assertAlmostEqual(transformed_tip[0], -math.sin(.5), places=6)
                self.assertAlmostEqual(transformed_tip[1], math.cos(.5), places=6)

    def test_sparse_axis_angle_rotation_fills_missing_axes_in_axis_angle_space(self):
        with tempfile.TemporaryDirectory() as directory:
            # rest Euler Z=.6 -> half-axis-angle (0,0,.3); override X=.4.
            rotations = self._export_sparse_axis_angle(directory, .6, rotation_x=.4)
            expected = (.8*math.sin(.5), 0., .6*math.sin(.5), math.cos(.5))
            for rotation in rotations:
                for actual, value in zip(rotation, expected):
                    self.assertAlmostEqual(actual, value, places=6)
                x, y, z, w = rotation
                transformed_tip = (2*(x*y-z*w), 1-2*(x*x+z*z), 2*(y*z+x*w))
                # Rodrigues rotation about axis (.8,0,.6), full angle 1 radian.
                expected_tip = (-.6*math.sin(1.), math.cos(1.), .8*math.sin(1.))
                for actual, value in zip(transformed_tip, expected_tip):
                    self.assertAlmostEqual(actual, value, places=6)

    def test_unknown_animation_section_is_explicit_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            model, motion = make_fixture(directory)
            data = bytearray(motion_bytes(unsupported=True))
            struct.pack_into("<I", data, 8+2*12, 5)  # Meaning unknown; reader does not support it.
            motion.write_bytes(pc_bytes(data))
            result = dispatch("export", {"path": str(model), "motion_paths": [str(motion)],
                                         "output_dir": str(Path(directory)/"out")})
            self.assertEqual(result["status"], "partial")
            self.assertFalse(result["animation_export_complete"])
            warning = next(w for w in result["warnings"] if w["code"] == "UNSUPPORTED_ANIMATION_CHANNELS")
            self.assertEqual(warning["sections"], [5])

    def test_empty_existing_output_directory_is_rejected_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            model, _ = make_fixture(directory)
            output = Path(directory)/"empty"
            output.mkdir()
            with self.assertRaises(AssetError) as context:
                dispatch("export", {"path": str(model), "output_dir": str(output)})
            self.assertEqual(context.exception.code, "OUTPUT_EXISTS")
            self.assertEqual(list(output.iterdir()), [])

    def test_existing_output_directory_with_unrelated_user_file_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            model, _ = make_fixture(directory)
            output = Path(directory)/"user-data"
            output.mkdir()
            (output/"notes.txt").write_bytes(b"preserve this user data")
            with self.assertRaises(AssetError) as context:
                dispatch("export", {"path": str(model), "output_dir": str(output)})
            self.assertEqual(context.exception.code, "OUTPUT_EXISTS")
            self.assertEqual([p.name for p in output.iterdir()], ["notes.txt"])
            self.assertEqual((output/"notes.txt").read_bytes(), b"preserve this user data")

    def test_exclusive_mkdir_rejects_directory_created_after_prechecks(self):
        with tempfile.TemporaryDirectory() as directory:
            model, _ = make_fixture(directory)
            output = Path(directory)/"raced"
            original_mkdir = Path.mkdir
            def race_mkdir(path, *args, **kwargs):
                if path == output:
                    original_mkdir(path, *args, **kwargs)
                return original_mkdir(path, *args, **kwargs)
            with mock.patch.object(Path, "mkdir", race_mkdir):
                with self.assertRaises(AssetError) as context:
                    dispatch("export", {"path": str(model), "output_dir": str(output)})
            self.assertEqual(context.exception.code, "OUTPUT_EXISTS")
            self.assertEqual(list(output.iterdir()), [])

    def test_relative_output_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            model, _ = make_fixture(directory)
            with self.assertRaises(AssetError) as context:
                dispatch("export", {"path": str(model), "output_dir": "relative-output"})
            self.assertEqual(context.exception.code, "INVALID_OUTPUT_PATH")

    def test_output_ancestor_links_are_checked_before_resolve(self):
        from rea3d_core.glb import _output_path
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/"link-parent"/"new-output"
            original_symlink = Path.is_symlink
            def is_link(path):
                return path == output.parent or original_symlink(path)
            with mock.patch.object(Path, "is_symlink", is_link), mock.patch.object(Path, "resolve") as resolve:
                with self.assertRaises(AssetError) as context:
                    _output_path(str(output))
                self.assertEqual(context.exception.code, "OUTPUT_LINK_PATH")
                resolve.assert_not_called()

    def test_output_ancestor_junction_is_rejected_before_resolve(self):
        from rea3d_core.glb import _output_path
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/"junction-parent"/"new-output"
            with mock.patch.object(Path, "is_junction", lambda path: path == output.parent, create=True), mock.patch.object(Path, "resolve") as resolve:
                with self.assertRaises(AssetError) as context:
                    _output_path(str(output))
                self.assertEqual(context.exception.code, "OUTPUT_LINK_PATH")
                resolve.assert_not_called()

    def test_fps_interprets_source_time_without_resampling(self):
        self.assertFalse(capabilities()["animation_timing"]["resampling"])
        self.assertEqual(capabilities()["animation_timing"]["default"], 30)
        with tempfile.TemporaryDirectory() as directory:
            model, motion = make_fixture(directory)
            result = dispatch("export", {"path": str(model), "motion_paths": [str(motion)], "fps": 60,
                                         "output_dir": str(Path(directory)/"out")})
            doc, binary = read_glb(result["glb_path"])
            sampler = doc["animations"][0]["samplers"][0]
            self.assertEqual(len(accessor(doc, binary, sampler["input"])), 3)
            self.assertAlmostEqual(accessor(doc, binary, sampler["input"])[-1][0], 2/60, places=7)
            self.assertEqual(doc["animations"][0]["extras"]["sourceFrameRate"], 60)
            self.assertFalse(doc["animations"][0]["extras"]["resampled"])
            self.assertFalse(result["animation_timing"]["resampling"])

    def test_compressed_container_uses_upstream_lz_and_pc_readers(self):
        with tempfile.TemporaryDirectory() as directory:
            model, _ = make_fixture(directory)
            original = model.read_bytes()
            model.write_bytes(lz10_literal_bytes(original))
            assets = read_assets(model)
            self.assertEqual(len(assets.models), 1)
            self.assertEqual(len(assets.textures), 1)
            self.assertIn("/LZ", assets.nodes[0]["label"])

    def test_invalid_motion_paths_argument_is_rejected(self):
        with self.assertRaises(AssetError) as context:
            dispatch("export", {"path": "unused", "motion_paths": "not-a-list"})
        self.assertEqual(context.exception.code, "INVALID_ARGUMENT")

    def test_real_upstream_reader_recovers_original_synthetic_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            model, _ = make_fixture(directory)
            assets = read_assets(model)
            self.assertEqual([n["format"] for n in assets.nodes], ["GFModel", "GFTexture"])
            self.assertFalse(assets.warnings)
            mesh = assets.models[0].submeshes[0]
            self.assertEqual(mesh.positions, [(0., 0., 0.), (1., 0., 0.), (0., 1., 0.)])
            self.assertEqual(mesh.bones, [[0], [1], [0, 1]])
            self.assertEqual(mesh.weights, [[1.], [1.], [.5, .5]])
            self.assertEqual(assets.textures[0].rgba[:4], bytes([128, 128, 128, 255]))

    def test_binary_to_glb_preserves_geometry_skin_texture_and_motion(self):
        with tempfile.TemporaryDirectory() as directory:
            model, motion = make_fixture(directory)
            result = dispatch("export", {"path": str(model), "motion_paths": [str(motion)],
                                         "output_dir": str(Path(directory)/"output")})
            self.assertEqual(result["status"], "converted_supported_data")
            self.assertEqual((result["mesh_count"], result["bone_count"], result["texture_count"], result["animation_count"]), (1, 2, 1, 1))
            self.assertTrue(result["animation_export_complete"])
            doc, binary = read_glb(result["glb_path"])
            for view in doc["bufferViews"]:
                self.assertEqual(view.get("byteOffset", 0) % 4, 0)
                self.assertLessEqual(view.get("byteOffset", 0)+view["byteLength"], doc["buffers"][0]["byteLength"])
            primitive = doc["meshes"][0]["primitives"][0]
            self.assertEqual(accessor(doc, binary, primitive["indices"]), [(0,), (1,), (2,)])
            self.assertEqual(accessor(doc, binary, primitive["attributes"]["TEXCOORD_0"]), [(0., 0.), (1., 0.), (0., 1.)])
            binds = accessor(doc, binary, doc["skins"][0]["inverseBindMatrices"])
            self.assertEqual(binds[1][13], -1.)  # Tip's inverse bind translates Y by -1.
            channels = doc["animations"][0]["channels"]
            translation_channel = next(c for c in channels if c["target"]["path"] == "translation")
            sampler = doc["animations"][0]["samplers"][translation_channel["sampler"]]
            self.assertEqual(accessor(doc, binary, sampler["output"]), [(0., 0., 0.), (1., 0., 0.), (2., 0., 0.)])
            times = accessor(doc, binary, sampler["input"])
            self.assertAlmostEqual(times[2][0], 2/30, places=6)
            self.assertEqual(Path(result["png_paths"][0]).read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(json.loads(Path(result["receipt_path"]).read_text())["sha256"], result["sha256"])

    def test_requested_unsupported_motion_fails_instead_of_claiming_success(self):
        with tempfile.TemporaryDirectory() as directory:
            model, motion = make_fixture(directory)
            motion.write_bytes(pc_bytes(motion_bytes(unsupported=True, skeletal=False)))
            with self.assertRaises(AssetError) as context:
                dispatch("export", {"path": str(model), "motion_paths": [str(motion)], "output_dir": str(Path(directory)/"out")})
            self.assertEqual(context.exception.code, "NO_SUPPORTED_MOTION")
            self.assertFalse((Path(directory)/"out").exists())

    def test_material_animation_is_reported_as_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            model, motion = make_fixture(directory)
            motion.write_bytes(pc_bytes(motion_bytes(unsupported=True)))
            result = dispatch("export", {"path": str(model), "motion_paths": [str(motion)], "output_dir": str(Path(directory)/"out")})
            self.assertEqual(result["status"], "partial")
            self.assertFalse(result["animation_export_complete"])
            self.assertIn("UNSUPPORTED_ANIMATION_CHANNELS", [w["code"] for w in result["warnings"]])

    def test_wrong_skeleton_motion_is_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            model, motion = make_fixture(directory)
            motion.write_bytes(pc_bytes(motion_bytes(name="Unrelated")))
            with self.assertRaises(AssetError) as context:
                dispatch("export", {"path": str(model), "motion_paths": [str(motion)], "output_dir": str(Path(directory)/"out")})
            self.assertEqual(context.exception.code, "NO_TRACKS_BOUND")
            self.assertFalse((Path(directory)/"out").exists())

    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            model, _ = make_fixture(directory)
            output = Path(directory)/"out"
            output.mkdir()
            (output/"model.glb").write_bytes(b"user-owned")
            with self.assertRaises(AssetError) as context:
                dispatch("export", {"path": str(model), "output_dir": str(output)})
            self.assertEqual(context.exception.code, "OUTPUT_EXISTS")
            self.assertEqual((output/"model.glb").read_bytes(), b"user-owned")

    def test_truncated_model_is_recorded_and_not_converted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"bad.gfmodel"
            path.write_bytes(model_bytes()[:80])
            inspected = dispatch("inspect", {"path": str(path)})
            self.assertIn("PARSE_FAILED", [w["code"] for w in inspected["warnings"]])
            with self.assertRaises(AssetError):
                dispatch("export", {"path": str(path), "output_dir": str(Path(directory)/"out")})

    def test_inverse_transform_handles_rotated_nonuniform_parent(self):
        matrix = local_matrix((2., 3., 4.), (.2, .3, -.4), (5., -2., 7.))
        product = multiply(matrix, inverse(matrix))
        for row in range(4):
            for column in range(4):
                self.assertAlmostEqual(product[row][column], float(row == column), places=10)


if __name__ == "__main__":
    unittest.main()
