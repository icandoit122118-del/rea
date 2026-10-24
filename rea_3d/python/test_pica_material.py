"""Original synthetic material fixtures and protocol/state regression tests."""
import copy
import hashlib
import json
import math
import struct
import unittest

from rea3d_core.pica_material import (
    PicaMaterialError, decode_pica_commands, inspect_bch_materials,
    read_bch_material_configs, texture_transform,
)


def command(register, values, *, mask=15, consecutive=False):
    values = list(values) if isinstance(values, (list, tuple)) else [values]
    words = [values[0], register | (mask << 16) | ((len(values)-1) << 20) | (int(consecutive) << 31), *values[1:]]
    if len(words) & 1:
        words.append(0)
    return struct.pack("<"+"I"*len(words), *words)


def fixture():
    """One model/material and a fully quantized 256-point LUT in BCH 0x21."""
    contents, contents_size, strings, commands = 68, 4096, 0x1100, 0x1400
    string_blob = bytearray()
    string_at = {}
    for value in ["model", "Eye", "eye1", "eye2", "eyeNor", "BodySkin", "table", "curve", "0@shader", "Eye@model"]:
        string_at[value] = strings+len(string_blob)
        string_blob.extend(value.encode()+b"\0")
    texture_stream = command(0x80, 7)
    fragment = bytearray()
    for stage, base in enumerate((0xC0, 0xC8, 0xD0, 0xD8, 0xF0, 0xF8)):
        for register, value in [(base, 0x03340EE4 if stage == 0 else 0x0FFF0FFF),
                                (base+1, 0x300 if stage == 0 else 0),
                                (base+2, 4 if stage == 0 else 0),
                                (base+3, 0x005E5E5E), (base+4, 0)]:
            fragment.extend(command(register, value))
    for register, value, mask in [(0xE0, 0x1100, 2), (0xFD, 0xFF000000, 15),
                                  (0x100, 0xE40100, 3), (0x101, 0x01010000, 15),
                                  (0x104, 0x8061, 3), (0x107, 0x1F51, 15), (0x40, 2, 15),
                                  (0x112, 15, 1), (0x113, 15, 1), (0x114, 2, 1), (0x115, 2, 1)]:
        fragment.extend(command(register, value, mask=mask))
    fragment.extend(command(0x2C0, 0x8000000A))
    fragment.extend(command(0x2C1, [0, 0, 0, 0]))
    lut_stream = command(0x1C5, 0) + command(0x1C8, [(i << 4) | ((i % 4) << 12) for i in range(256)])
    fragment_at = commands+len(texture_stream)
    lut_at = fragment_at+len(fragment)
    command_blob = texture_stream+fragment+lut_stream
    end = commands+len(command_blob)
    data = bytearray(end)
    data[:4] = b"BCH\0"
    data[4:6] = bytes([0x21, 0x21])
    struct.pack_into("<12i", data, 8, contents, strings, commands, end, end, end,
                     contents_size, len(string_blob), len(command_blob), 0, 0, 0)
    data[64] = 1
    data[strings:strings+len(string_blob)] = string_blob
    data[commands:] = command_blob
    # Models dictionary, indirect model pointer and model Patricia tree.
    struct.pack_into("<III", data, contents, 0x180, 1, 0x190)
    struct.pack_into("<I", data, 0x180, 0x200)
    struct.pack_into("<IHHI", data, 0x190+12, 0, 0, 0, string_at["model"])
    struct.pack_into("<III", data, 0x200+0x34, 0x300, 1, 0)
    struct.pack_into("<II", data, 0x200+0x40, 0x530, 1)
    struct.pack_into("<II", data, 0x200+0x7C, 0x500, 1)
    struct.pack_into("<II", data, 0x200+0x88, 1, 0x510)
    struct.pack_into("<I", data, 0x500, 1)
    struct.pack_into("<IHHI", data, 0x510+12, 0, 0, 0, string_at["BodySkin"])
    # 0x21 material header: 44 bytes, references its 0x110-byte parameter block.
    at, params, mapper = 0x300, 0x340, 0x450
    struct.pack_into("<I", data, at, params)
    struct.pack_into("<III", data, at+0x10, commands, len(texture_stream)//4, mapper)
    for i, name in enumerate(("eye1", "eye2", "eyeNor", "Eye")):
        struct.pack_into("<I", data, at+0x1C+i*4, string_at[name])
    struct.pack_into("<HBB", data, params+4, 1, 16, 0)
    for i in range(3):
        struct.pack_into("<3Bb5f", data, params+12+24*i, 1, 0, 0, -1, 2 if i == 0 else 1, 1, 0, 1 if i == 0 else 0, 0)
        struct.pack_into("<6B2xfI", data, mapper+16*i, i, 3, 2, 0, 2, 0, 0.0, 0xFF000000)
    for i in range(12):
        struct.pack_into("<I", data, params+0x58+i*4, 0x005E5E5E)
    struct.pack_into("<f", data, params+0x88, 1.0)
    data[params+0xA4:params+0xA8] = bytes([4, 2, 1, 2])
    inline_lut = command(0x1D0, 0x02222222)+command(0x1D1, 0x02220000)+command(0x1D2, 0)
    data[params+0xA8:params+0xC0] = inline_lut
    struct.pack_into("<I", data, params+0xC0, 0x500200)
    struct.pack_into("<III", data, params+0xC8, fragment_at, len(fragment)//4, fragment_at)
    struct.pack_into("<I", data, params+0xE0, string_at["table"])
    struct.pack_into("<I", data, params+0xF8, string_at["curve"])
    struct.pack_into("<II", data, params+0x104, string_at["0@shader"], string_at["Eye@model"])
    # One LUT table object, containing an inline 16-byte sampler.
    struct.pack_into("<III", data, contents+4*12, 0x620, 1, 0x630)
    struct.pack_into("<I", data, 0x620, 0x600)
    struct.pack_into("<IHHI", data, 0x630+12, 0, 0, 0, string_at["table"])
    struct.pack_into("<III", data, 0x600, 0x610, 1, string_at["table"])
    struct.pack_into("<4I", data, 0x610, 0, lut_at, len(lut_stream)//4, string_at["curve"])
    return data, {"material": at, "params": params, "mapper": mapper, "fragment": fragment_at, "lut": lut_at,
                  "commands": commands, "end": end}


class CommandTests(unittest.TestCase):
    def test_multi_parameter_and_consecutive_commands_keep_real_byte_offsets(self):
        blob = bytes(12)+command(0xC0, [11, 22, 33], consecutive=True)+command(0x1C8, [44, 55, 66])
        result = decode_pica_commands(blob, 12, (len(blob)-12)//4)
        self.assertEqual([(w["register"], w["value"], w["source_offset"]) for w in result["writes"]],
                         [(0xC0, 11, 12), (0xC1, 22, 20), (0xC2, 33, 24),
                          (0x1C8, 44, 28), (0x1C8, 55, 36), (0x1C8, 66, 40)])
        self.assertEqual(result["sha256"], hashlib.sha256(blob[12:]).hexdigest())

    def test_masked_register_writes_merge_known_bytes_without_invented_defaults(self):
        blob = command(0x104, 0xAABBCCDD, mask=1)+command(0x104, 0x11223344, mask=2)
        result = decode_pica_commands(blob, 0, len(blob)//4)
        self.assertEqual(result["registers"][str(0x104)]["value"], 0x33DD)
        self.assertEqual(result["registers"][str(0x104)]["known_mask"], 0xFFFF)
        self.assertEqual(result["registers"][str(0x104)]["source_offsets"], [0, 8])

    def test_float32_and_float24_uniforms_are_xyzw_and_auto_increment(self):
        bits = [struct.unpack("<I", struct.pack("<f", v))[0] for v in [4., 3., 2., 1.]]
        words = command(0x2C0, 0x8000000A)+command(0x2C1, bits)
        words += command(0x2C0, 11)+command(0x2C1, [0x3E00003F, 0x00003F00, 0x003F0000])
        report = decode_pica_commands(words, 0, len(words)//4)
        self.assertEqual(report["vertex_uniforms"]["10"], [1., 2., 3., 4.])
        self.assertEqual(report["vertex_uniforms"]["11"], [1., 1., 1., .5])

    def test_truncated_parameters_alignment_and_input_bounds_are_rejected(self):
        for blob, count in [(b"\0"*4, 1), (struct.pack("<II", 0, 1 << 20), 2),
                            (struct.pack("<III", 0, 1 << 20, 0), 3), (b"", 1)]:
            with self.subTest(blob=blob), self.assertRaises(PicaMaterialError):
                decode_pica_commands(blob, 0, count)


class MaterialTests(unittest.TestCase):
    def test_static_materials_pipeline_lut_and_node_visibility_survive(self):
        blob, positions = fixture()
        original = bytes(blob)
        report = inspect_bch_materials(blob)
        self.assertEqual(bytes(blob), original)
        json.dumps(report, allow_nan=False)
        model = report["models"][0]
        self.assertEqual(model["name"], "model")
        self.assertEqual(model["mesh_visibility"]["by_node_name"], {"BodySkin": True})
        self.assertEqual(model["mesh_visibility"]["meshes"][0]["node_name"], "BodySkin")
        material = model["materials"][0]
        self.assertEqual(material["texture_names"], ["eye1", "eye2", "eyeNor"])
        self.assertEqual(material["texture_sources"], [0., 0., 0., 0.])
        self.assertEqual(material["tex_coords"][0]["affine_rows"], [[2., -0., -2.], [0., 1., 0.]])
        self.assertEqual(material["texture_mappers"][0]["wrap_u"], 3)
        self.assertEqual(material["texture_mappers"][0]["min_filter"], 2)
        self.assertEqual(material["texenv_stages"][0]["color_sources"], ["Texture1", "Constant", "Constant"])
        self.assertEqual(material["texenv_stages"][0]["color_operands"], ["Color", "Color", "OneMinusAlpha"])
        self.assertEqual(material["texenv_stages"][0]["color_combiner"], "Interpolate")
        self.assertEqual([s["constant_index"] for s in material["texenv_stages"]], [0, 0, 2, 0, 0, 5])
        self.assertTrue(material["texenv_stages"][1]["update_color_buffer"])
        self.assertTrue(material["texenv_stages"][1]["update_alpha_buffer"])
        self.assertFalse(material["texenv_stages"][0]["update_color_buffer"])
        self.assertEqual(material["alpha_test"], {"enabled": True, "function": "Greater", "function_code": 6, "reference": 128})
        self.assertEqual(material["blend"]["color_src"], "One")
        self.assertEqual(material["blend"]["color_dst"], "Zero")
        self.assertEqual(material["depth_color_mask"]["depth_function"], "Lequal")
        self.assertEqual(material["face_culling"], "BackFace")
        self.assertTrue(material["lighting"]["lut_reflection_enabled"])
        self.assertFalse(material["lut_config"]["dist0"]["absolute"])
        self.assertEqual(material["lut_references"]["reflect_r"]["sampler"], "curve")
        sampler = report["luts"][0]["samplers"][0]
        self.assertTrue(sampler["complete"])
        self.assertEqual(sampler["decoded"][0], 0.)
        self.assertAlmostEqual(sampler["decoded"][-1], 4080/4095)
        self.assertEqual(sampler["difference_codes"][3], 3)
        self.assertEqual(sampler["value_source_offsets"][0], positions["lut"]+8)
        self.assertEqual(material["source_offsets"]["params"], positions["params"])
        self.assertFalse(report["visual_parity_verified"])
        self.assertFalse(report["original_shader_bytecode_executed"])
        self.assertEqual(read_bch_material_configs(blob)[0]["model_name"], "model")

    def test_unknown_combiner_is_explicitly_unsupported(self):
        blob, positions = fixture()
        # Each standalone command is 8 bytes; stage0 combiner follows source and operand.
        struct.pack_into("<I", blob, positions["fragment"]+16, 15)
        material = inspect_bch_materials(blob)["models"][0]["materials"][0]
        self.assertFalse(material["static_state_decoded"])
        self.assertEqual(material["texenv_stages"][0]["color_combiner"], "Unknown(15)")
        self.assertIn("UNKNOWN_ENUM", [u["code"] for u in material["unsupported"]])

    def test_missing_stage_bits_remain_null_and_partial(self):
        blob, positions = fixture()
        # Mask zero means source state is not provided by this command stream.
        struct.pack_into("<I", blob, positions["fragment"]+4, 0xC0)
        material = inspect_bch_materials(blob)["models"][0]["materials"][0]
        self.assertEqual(material["texenv_stages"][0]["color_sources"], [None, None, None])
        self.assertFalse(material["static_state_decoded"])

    def test_missing_alpha_pipeline_does_not_silently_become_disabled(self):
        blob, positions = fixture()
        needle = command(0x104, 0x8061, mask=3)
        at = blob.find(needle, positions["fragment"])
        self.assertGreaterEqual(at, 0)
        struct.pack_into("<I", blob, at+4, 0x104)  # zero write mask
        material = inspect_bch_materials(blob)["models"][0]["materials"][0]
        self.assertIsNone(material["alpha_test"]["enabled"])
        self.assertFalse(material["static_state_decoded"])
        self.assertIn("INCOMPLETE_PIPELINE_STATE", [u["code"] for u in material["unsupported"]])

    def test_bounds_version_nonfinite_and_repeat_pointer_are_rejected(self):
        cases = [("params pointer", lambda b,p: struct.pack_into("<I", b, p["material"], len(b)-4)),
                 ("command extent", lambda b,p: struct.pack_into("<I", b, p["params"]+0xCC, 99999)),
                 ("repeat pointer", lambda b,p: struct.pack_into("<I", b, p["params"]+0xD0, p["fragment"]+4)),
                 ("nonfinite", lambda b,p: struct.pack_into("<f", b, p["params"]+16, float("nan"))),
                 ("version", lambda b,p: b.__setitem__(4, 0x20)),
                 ("LUT write overflow", lambda b,p: struct.pack_into("<I", b, p["lut"], 255))]
        for label, mutate in cases:
            with self.subTest(label=label):
                blob, positions = fixture()
                mutate(blob, positions)
                with self.assertRaises(PicaMaterialError):
                    inspect_bch_materials(blob)

    def test_texture_transform_uses_source_maya_center_and_translation_semantics(self):
        maya = texture_transform([2., 3.], math.pi/2, [.25, .5], 0)
        self.assertAlmostEqual(maya[0][0], 0.)
        self.assertAlmostEqual(maya[0][1], -2.)
        self.assertAlmostEqual(maya[0][2], 1.5)
        self.assertAlmostEqual(maya[1][0], 3.)
        self.assertAlmostEqual(maya[1][2], -1.5)
        self.assertIsNone(texture_transform([1., 1.], 0., [0., 0.], 99))


if __name__ == "__main__":
    unittest.main()
