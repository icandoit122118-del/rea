"""Original synthetic BCH animation fixtures plus optional selected-file checks.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import json
import hashlib
import math
import os
from pathlib import Path
import struct
import unittest

from rea3d_core.bch_animation import (Animation, BooleanCurve, Curve, Element, Key, TARGETS,
    evaluate_animation, parse_bch, parse_pokemon_pack, read_curve, read_pokemon_animation_package, sample_pack_clip)


def build_bch(material=True, visibility=True, target=21, primitive=2, animated=True):
    data = bytearray(0x1000)
    addresses = [68, 0xe00, 0x1000, 0x1000, 0x1000, 0x1000]
    lengths = [180, 0x200, 0, 0, 0, 0]
    struct.pack_into("<4sBBH6i6i2iBBH", data, 0, b"BCH\0", 0x21, 0x21, 0, *addresses, *lengths, 0, 0, 0, 0, 0)
    data[0xe00:0xe06] = b"Blink\0"
    data[0xe20:0xe24] = b"Eye\0"
    data[0xe40:0xe46] = b"Vines\0"
    if material:
        struct.pack_into("<III", data, 68+9*12, 0x180, 1, 0)
        struct.pack_into("<I", data, 0x180, 0x200)
        struct.pack_into("<IBBHfIII", data, 0x200, 0xe00, 1, 1, 0, 2., 0x240, 1, 0)
        struct.pack_into("<II", data, 0x218, 0, 0)
        struct.pack_into("<I", data, 0x240, 0x260)
        struct.pack_into("<IHH", data, 0x260, 0xe20, target, primitive)
        if animated:
            struct.pack_into("<IIf", data, 0x268, 0xc02, 0x500, 0.)
            struct.pack_into("<ffBBHBBHffffI", data, 0x500, 0., 2., 0, 0, 0, 0, 6, 3, 1., 0., 1., .5, 0x540)
            for index, (frame, value) in enumerate([(0.,0.),(1.,2.),(2.,0.)]):
                struct.pack_into("<ff", data, 0x540+index*8, frame, value)
        else:
            struct.pack_into("<Iff", data, 0x268, 0xc03, 1., .5)
    if visibility:
        struct.pack_into("<III", data, 68+10*12, 0x184, 1, 0)
        struct.pack_into("<I", data, 0x184, 0x300)
        struct.pack_into("<IBBHfIII", data, 0x300, 0xe00, 0, 2, 0, 2., 0x340, 1, 0)
        struct.pack_into("<I", data, 0x340, 0x360)
        struct.pack_into("<IHH", data, 0x360, 0xe40, 47, 8)
        struct.pack_into("<ffBBHII", data, 0x368, 0.,2.,0,0,0,0x600,3)
        struct.pack_into("<I", data, 0x600, 0b010)
    return bytes(data)


def build_package(outside_primary=False):
    # N=1: entry0 GF1 slots, entry1 bounds, entry2 visibility, entries3/4 material.
    # The GF1 motion payload needs only its real header for association validation.
    gf1 = struct.pack("<3I", 2, 12, 16)+bytes(4)+struct.pack("<HH", 2, 2)
    if outside_primary:
        # An unrecognized out-of-primary slot must not inherit the selected
        # ORAS auxiliary role merely because its index is record_count+7.
        gf1 = struct.pack("<11I", 10, 44, 48, *([0]*7), 52)+bytes(4)+struct.pack("<4H",2,2,2,2)
    bounds = struct.pack("<6f", -1,-2,-3,1,2,3)
    entries = [gf1,bounds,build_bch(False,True),build_bch(True,False),b""]
    offset = 4+4*(len(entries)+1)
    offsets = [offset]
    for entry in entries:
        offsets.append(offsets[-1]+len(entry))
    return b"PB"+struct.pack("<H",len(entries))+struct.pack("<6I",*offsets)+b"".join(entries)


class BCHAnimationTests(unittest.TestCase):
    def test_constant_vector_and_boolean_bitpacking(self):
        result = parse_bch(build_bch(animated=False))
        self.assertEqual(result["errors"],[])
        animation = result["material"][0]
        self.assertEqual(animation.errors,[])
        self.assertEqual(evaluate_animation(animation,1)[0]["values"],[1.,.5])
        visibility = result["visibility"][0]
        self.assertEqual(evaluate_animation(visibility,0)[0]["values"],[False])
        self.assertEqual(evaluate_animation(visibility,1)[0]["values"],[True])
        self.assertEqual(evaluate_animation(visibility,2)[0]["values"],[False])
        self.assertEqual(evaluate_animation(visibility,200)[0]["values"],[False])
        self.assertEqual(evaluate_animation(visibility,1)[0]["target"],"mesh_node_visibility")

    def test_step_curve_does_not_become_linear_or_hermite(self):
        result = parse_bch(build_bch())
        animation = result["material"][0]
        self.assertEqual(evaluate_animation(animation,.5)[0]["values"],[0.,0.])
        self.assertEqual(evaluate_animation(animation,1.5)[0]["values"],[2.,0.])
        self.assertEqual(evaluate_animation(animation,2)[0]["values"],[0.,0.])

    def test_linear_curve_and_separate_hermite_in_out_slopes(self):
        linear = Curve([Key(0,0),Key(2,4)],"linear")
        self.assertEqual(linear.sample(.5),1.)
        hermite = Curve([Key(0,0,in_slope=999,out_slope=4),Key(2,4,in_slope=0,out_slope=-999)],"hermite")
        self.assertEqual(hermite.sample(1),3.)
        self.assertEqual(hermite.to_dict()["keys"][0]["out_slope"],4)

    def test_curve_wire_keeps_distinct_hermite_slopes(self):
        data = bytearray(128)
        struct.pack_into("<ffBBHBBHffffI",data,0,0,2,0,0,0,2,0,2,1,0,1,.5,64)
        struct.pack_into("<4f",data,64,0,0,999,4)
        struct.pack_into("<4f",data,80,2,4,0,-999)
        curve = read_curve(data,0,0x21)
        self.assertEqual(curve.sample(1),3.)

    def test_texture_selection_and_rgba_missing_channel(self):
        animation = Animation("swap","material",0,4,texture_names=["EyeOpen","EyeClosed"],elements=[
            Element("Eye",13,6,[Curve([Key(0,0),Key(1,1)],"step")]),
            Element("Eye",9,5,[Curve([Key(0,.2)]),None,None,Curve([Key(0,1.)])])])
        values = evaluate_animation(animation,1)
        self.assertEqual(values[0]["texture_name"],"EyeClosed")
        self.assertEqual(values[1]["values"],[.2,None,None,1.])

    def test_wire_texture_selection_and_color_channels(self):
        data = bytearray(build_bch(target=13,primitive=6,visibility=False))
        struct.pack_into("<II",data,0x218,0x700,3)
        for index,label in enumerate(["open","half","closed"]):
            at = 0xe60+index*32
            struct.pack_into("<I",data,0x700+4*index,at)
            data[at:at+len(label)+1]=label.encode()+b"\0"
        result = parse_bch(data)
        animation = result["material"][0]
        self.assertEqual(animation.errors,[])
        self.assertEqual(evaluate_animation(animation,1.5)[0]["texture_name"],"closed")
        color = parse_bch(build_bch(target=9,primitive=5,visibility=False))["material"][0]
        self.assertEqual(color.errors,[])
        self.assertEqual(evaluate_animation(color,1.5)[0]["values"],[2.,0.,None,None])

    def test_wrong_primitive_for_target_is_rejected(self):
        result = parse_bch(build_bch(target=13,primitive=2))
        self.assertEqual(len(result["material"][0].errors),1)

    def test_unsupported_element_is_explicit_not_successful_empty(self):
        result = parse_bch(build_bch(primitive=9))
        self.assertEqual(len(result["material"]),1)
        self.assertEqual(result["material"][0].elements,[])
        self.assertEqual(len(result["material"][0].errors),1)

    def test_bad_boolean_pointer_is_an_element_error(self):
        data = bytearray(build_bch())
        struct.pack_into("<I",data,0x368+12,0xFFFF)
        result = parse_bch(data)
        self.assertEqual(len(result["visibility"][0].errors),1)

    def test_bounds_record_drives_corroborated_slot_group_mapping(self):
        result = parse_pokemon_pack(build_package())
        self.assertEqual(result["record_count"],1)
        mapping = result["mappings"][0]
        self.assertEqual(mapping["visibility_entry"],2)
        self.assertEqual(mapping["material_entries"],[3,4])
        self.assertEqual(mapping["clip_name"],"Blink")
        self.assertEqual(mapping["warnings"],[])
        sample = sample_pack_clip(result,0,1)
        self.assertEqual(sample["materials"][0]["values"],[2.,0.])
        self.assertEqual(sample["visibility"][0]["values"],[True])

    def test_json_package_contract_is_serializable(self):
        result = read_pokemon_animation_package(build_package(),"test")
        encoded = json.dumps(result,allow_nan=False)
        decoded = json.loads(encoded)
        self.assertEqual(decoded["mappings"][0]["material_animations"][0]["elements"][0]["target"],"material_texcoord0_translation")

    def test_unknown_outside_primary_is_not_given_a_fake_auxiliary_role(self):
        result = parse_pokemon_pack(build_package(outside_primary=True))
        auxiliary = next(item for item in result["mappings"] if item["source_slot"] == 8)
        self.assertNotIn("role",auxiliary)
        self.assertEqual(auxiliary["warnings"][0]["code"],"AUXILIARY_SKELETAL_SLOT_UNMAPPED")
        self.assertEqual(auxiliary["material_animations"],[])
        self.assertEqual(auxiliary["visibility_animation"],[])
        self.assertFalse(auxiliary["association_verified"])

    def test_selected_shared_auxiliary_identity_mask_and_motion_samples(self):
        from ctr.compress.lz import decompress_lz
        from rea3d_core.gf1 import read_pack
        selected = os.environ.get('REA3D_ORAS_MEMBERS_DIR')
        if not selected:
            self.skipTest('REA3D_ORAS_MEMBERS_DIR was not explicitly selected')
        root = Path(selected)
        if not root.is_dir():
            self.skipTest("selected ORAS input is not installed on this host")
        payloads = []
        expected_jaw = [-.22362995147705078,-.46797606348991394,-.7297754287719727,
                        -.22362995147705078,-.46797606348991394,-.22362995147705078]
        for name,slot in [("00008_00.bin",25),("00009_00.bin",33)]:
            with self.subTest(name=name):
                raw = decompress_lz((root/name).read_bytes())
                entry0,end = struct.unpack_from("<II",raw,4)
                gf1 = raw[entry0:end]
                result = parse_pokemon_pack(raw,name)
                mapping = next(item for item in result["mappings"] if item["source_slot"] == slot)
                self.assertEqual(mapping["role"],"auxiliary_pose")
                self.assertEqual(mapping["association_kind"],"shared_auxiliary_pose")
                self.assertFalse(mapping["association_verified"])
                self.assertEqual(mapping["material_entries"],[])
                self.assertIsNone(mapping["visibility_entry"])
                self.assertEqual(mapping["material_animations"],[])
                self.assertEqual(mapping["visibility_animation"],[])
                meta = mapping["auxiliary_pose"]
                self.assertFalse(meta["automatic_base_association_verified"])
                self.assertEqual(meta["gf1_first_bone_index"],0)
                self.assertEqual(meta["source_frame_count"],71)
                self.assertEqual(meta["channel_mask"],[{"bone":bone,"path":"rotation","axes":[0,1,2]}
                    for bone in ["LFoot","RFoot","Feeler","Jaw"]])
                self.assertEqual(meta["variable_targets"],[{"bone":"Jaw","path":"rotation","axes":[1]}])
                self.assertEqual(len(meta["constant_targets"]),11)
                self.assertEqual(mapping["warnings"][0]["code"],"AUXILIARY_BASE_CLIP_SELECTION_UNVERIFIED")
                payload = gf1[meta["payload_offset"]:meta["payload_offset"]+meta["payload_length"]]
                self.assertEqual(len(payload),136)
                self.assertEqual(hashlib.sha256(payload).hexdigest(),meta["payload_sha256"])
                payloads.append(payload)
                parsed = read_pack(gf1)
                motion = next(motion for motion in parsed["motions"] if motion.index == slot)
                self.assertEqual([bone.name for bone in motion.bones],["LFoot","RFoot","Feeler","Jaw"])
                for bone in motion.bones:
                    self.assertFalse(any(curve.keys for curve in (*bone.translation,*bone.scale)))
                for bone,expected in zip(motion.bones[:3],[-1.6664425134658813,-1.6664425134658813,1.5753768682479858]):
                    self.assertEqual([curve.sample(0) for curve in bone.rotation],[0.,expected,0.])
                    self.assertEqual([curve.sample(70) for curve in bone.rotation],[0.,expected,0.])
                jaw = motion.bones[3].rotation
                self.assertEqual(jaw[0].sample(0),math.pi)
                self.assertEqual(jaw[2].sample(70),0.)
                self.assertEqual([jaw[1].sample(frame) for frame in [0,10,20,30,40,50]],expected_jaw)
                self.assertEqual(jaw[1].sample(70),expected_jaw[0])
                self.assertAlmostEqual(jaw[1].sample(9.5),(expected_jaw[0]+expected_jaw[1])/2)
                # Same payload with a different source binding cannot inherit
                # the corroborated source role. This checks skeleton indices,
                # not just a byte hash or slot-number heuristic.
                altered = bytearray(raw)
                skeleton_name = altered.find(b"Jaw\0",entry0,end)
                self.assertGreater(skeleton_name,entry0)
                altered[skeleton_name:skeleton_name+3] = b"Eye"
                changed = parse_pokemon_pack(bytes(altered),name)
                rejected = next(item for item in changed["mappings"] if item["source_slot"] == slot)
                self.assertNotIn("role",rejected)
        self.assertEqual(payloads[0],payloads[1])

    def test_selected_oras_files_when_present(self):
        selected = os.environ.get('REA3D_ORAS_MEMBERS_DIR')
        if not selected:
            self.skipTest('REA3D_ORAS_MEMBERS_DIR was not explicitly selected')
        root = Path(selected)
        if not root.is_dir():
            self.skipTest("selected ORAS input is not installed on this host")
        for name,n,material_entry,count in [("00008_00.bin",18,20,10),("00009_00.bin",26,28,20)]:
            with self.subTest(name=name):
                result = parse_pokemon_pack((root/name).read_bytes(),name)
                self.assertEqual(result["record_count"],n)
                self.assertEqual(len(result["mappings"]),count)
                self.assertEqual(result["errors"],[])
                for entry in result["entries"].values():
                    self.assertEqual(entry["errors"],[])
                    for kind in ["material","visibility"]:
                        for animation in entry[kind]:
                            self.assertEqual(animation.errors,[])
                mapping = next(m for m in result["mappings"] if m["source_slot"]==0)
                self.assertEqual(mapping["material_entries"][0],material_entry)
                sample = sample_pack_clip(result,0,0)
                values = {e["name"]:e["values"][0] for e in sample["visibility"]}
                self.assertTrue(values["pm0001_00_BodySkin"])
                self.assertFalse(values["pm0001_00_LTutaSkin"])
                self.assertFalse(values["pm0001_00_RTutaSkin"])
                self.assertEqual(len(sample["materials"]),9)
                if name == "00009_00.bin":
                    drowse = next(m for m in result["mappings"] if m["source_slot"] == 4)
                    eye = next(e for a in drowse["material_animations"] for e in a.elements if e.name == "Eye" and e.target_id == 21)
                    # Preserve axis identity: X is the present constant 1; Y is
                    # the animated atlas row. Never label the first varying axis X.
                    self.assertEqual(len(eye.curves[0].keys),1)
                    self.assertEqual(eye.curves[0].sample(15),1.)
                    self.assertEqual(eye.curves[1].sample(0),0.)
                    self.assertEqual(eye.curves[1].sample(10),.25)
                    self.assertEqual(eye.curves[1].sample(15),.5)


if __name__ == "__main__":
    unittest.main()
