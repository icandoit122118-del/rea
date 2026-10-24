"""Focused CPU/actual-GPU tests; all fixtures and artifacts stay on D:."""
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode=True
spec=importlib.util.spec_from_file_location("pica_renderer",Path(__file__).resolve().parents[1]/"scripts"/"render-pica-glb.py")
renderer=importlib.util.module_from_spec(spec);spec.loader.exec_module(renderer)
np=renderer.np


def fixture_glb(path):
    chunks=[];views=[];accessors=[];offset=0
    def view(data):
        nonlocal offset
        data=bytes(data);padding=(-len(data))%4
        index=len(views);views.append({"buffer":0,"byteOffset":offset,"byteLength":len(data)});chunks.append(data+b"\0"*padding);offset+=len(data)+padding
        return index
    def accessor(values,kind="VEC3",component=5126,dtype="<f4"):
        array=np.asarray(values,dtype=dtype)
        index=len(accessors);accessors.append({"bufferView":view(array.tobytes()),"componentType":component,"count":len(array),"type":kind});return index
    positions=accessor([[0,0,0],[1,0,0],[0,1,0],[999,999,999]])
    normals=accessor([[0,0,1]]*4)
    tangents=accessor([[1,0,0]]*4)
    uv=accessor([[0,0],[1,0],[0,1],[1,1]],"VEC2")
    weights=accessor([[1,0,0,0]]*4,"VEC4")
    joints=accessor([[0,0,0,0]]*4,"VEC4",5123,"<u2")
    indices=accessor([[0],[1],[2]],"SCALAR",5123,"<u2")
    inverse=accessor([np.eye(4).reshape(-1)],"MAT4")
    times=accessor([[0],[1]],"SCALAR")
    translations=accessor([[0,0,0],[1,0,0]])
    png=renderer.gl_helpers.png_rgba_bytes(1,1,b"\xff"*4)
    image_view=view(png)
    document={"asset":{"version":"2.0"},"scene":0,"scenes":[{"nodes":[0]}],
              "nodes":[{"name":"joint","children":[1]},{"name":"mesh","mesh":0,"skin":0,"extras":{"reaPicaNodeName":"source_body"}}],
              "meshes":[{"primitives":[{"attributes":{"POSITION":positions,"NORMAL":normals,"_PICA_TANGENT":tangents,"TEXCOORD_0":uv,"JOINTS_0":joints,"WEIGHTS_0":weights},"indices":indices,"material":0}]}],
              "skins":[{"joints":[0],"inverseBindMatrices":inverse}],"buffers":[{"byteLength":offset}],"bufferViews":views,"accessors":accessors,
              "images":[{"name":"white","bufferView":image_view,"mimeType":"image/png"}],"textures":[{"source":0}],
              "materials":[{"name":"mat","doubleSided":True,"pbrMetallicRoughness":{"baseColorTexture":{"index":0}}}],
              "animations":[{"name":"translate","samplers":[{"input":times,"output":translations,"interpolation":"LINEAR"}],"channels":[{"sampler":0,"target":{"node":0,"path":"translation"}}]}]}
    encoded=json.dumps(document,separators=(",",":")).encode();encoded+=b" "*((-len(encoded))%4);binary=b"".join(chunks)
    total=12+8+len(encoded)+8+len(binary)
    path.write_bytes(struct.pack("<5I",0x46546c67,2,total,len(encoded),0x4e4f534a)+encoded+struct.pack("<2I",len(binary),0x004e4942)+binary)


class RendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory=Path(tempfile.mkdtemp(prefix="pica-gpu-tests-",dir=renderer.ROOT))
        cls.model=cls.directory/"synthetic.glb";fixture_glb(cls.model)

    def test_slerp_shortest_quaternion_arc_and_trs(self):
        q=renderer.quaternion_slerp([0,0,0,1],[0,0,1,0],.5)
        transformed=renderer.trs_matrix([1,2,3],q,[1,1,1])@np.array([1,0,0,1])
        np.testing.assert_allclose(transformed,[1,3,3,1],atol=1e-7)
        np.testing.assert_allclose(renderer.quaternion_slerp([0,0,0,1],[0,0,0,-1],.5),[0,0,0,1])

    def test_png_rows_are_preserved_and_crc_is_verified(self):
        bottom=b"\xff\0\0\xff"+b"\0\xff\0\xff"
        png=renderer.gl_helpers.png_rgba_bytes(1,2,bottom)
        width,height,raw=renderer.decode_png(png)
        self.assertEqual((width,height),(1,2));self.assertEqual(raw,bottom[4:]+bottom[:4])
        broken=bytearray(png);broken[20]^=1
        with self.assertRaisesRegex(ValueError,"CRC"):renderer.decode_png(broken)

    def test_skin_pose_override_custom_tangent_and_referenced_bounds(self):
        model=renderer.Glb(self.model)
        base,_=model.evaluate({"clip_index":0,"time_seconds":0})
        animated,_=model.evaluate({"clip_index":0,"time_seconds":.5})
        fixed,_=model.evaluate({"clip_index":0,"time_seconds":.5,"pose_time_seconds":0})
        np.testing.assert_allclose(animated[0]["positions"]-base[0]["positions"],np.tile([.5,0,0],(4,1)),atol=1e-6)
        np.testing.assert_array_equal(fixed[0]["positions"],base[0]["positions"])
        self.assertEqual(base[0]["tangent_source"],"_PICA_TANGENT")
        self.assertEqual(renderer.bounds(base)["max"],[1.,1.,0.])
        self.assertFalse(renderer.visibility_for(base[0],{"source_body":False}))

    def test_actual_gpu_fixed_pose_material_change_motion_and_visibility(self):
        mapper={"wrap_u":3,"wrap_v":2,"mag_filter":0,"min_filter":2,"min_lod":0,"lod_bias":0}
        config={"texture_mappers":[mapper,{**mapper,"wrap_u":2},{**mapper,"wrap_u":0,"wrap_v":0}],"depth_func":"Lequal","blend":{"enabled":True,"src_rgb":"One","dst_rgb":"Zero","src_alpha":"One","dst_alpha":"Zero","equation_rgb":"FuncAdd","equation_alpha":"FuncAdd"}}
        plan={"glb":str(self.model),"width":128,"height":128,"camera_bounds":{"min":[-.5,-.5,-.5],"max":[2,1.5,.5]},"materials":{"mat":{"config":config}},"frames":[
            {"clip_index":0,"time_seconds":0},
            {"clip_index":0,"time_seconds":.5,"pose_time_seconds":0,"material_states":{"mat":{"colors":{"diffuse":[1,.2,.2,1]}}}},
            {"clip_index":0,"time_seconds":.5},
            {"clip_index":0,"time_seconds":.5,"visibility":{"source_body":False}}]}
        plan_path=self.directory/"plan.json";plan_path.write_text(json.dumps(plan),encoding="utf-8")
        output=self.directory/"gpu"
        environment=dict(os.environ,PYTHONDONTWRITEBYTECODE="1",TEMP=str(self.directory),TMP=str(self.directory),TMPDIR=str(self.directory))
        run=subprocess.run([sys.executable,str(Path(renderer.__file__)),"--plan",str(plan_path),"--output-dir",str(output)],env=environment,capture_output=True,text=True,timeout=90)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)
        receipt=json.loads((output/"pica-glb-render-receipt.json").read_text(encoding="utf-8"))
        self.assertTrue(receipt["ok"]);self.assertTrue(receipt["inputs_unchanged"])
        self.assertEqual(receipt["camera"]["fit_source"],"explicit plan.camera_bounds")
        self.assertEqual(receipt["camera"]["bounds_union_visible_posed"],plan["camera_bounds"])
        frames=receipt["frames"]
        self.assertEqual(frames[0]["geometry"]["world_positions_sha256"],frames[1]["geometry"]["world_positions_sha256"])
        self.assertGreater(frames[1]["pixel_difference_from_first_frame"]["changed_pixels"],0)
        self.assertEqual(frames[2]["geometry"]["moved_vertices_from_first_frame"],3)
        self.assertAlmostEqual(frames[2]["geometry"]["max_vertex_displacement_from_first_frame"],.5)
        self.assertEqual(frames[3]["visible_meshes"],[]);self.assertEqual(frames[3]["draws"],[])
        self.assertEqual(frames[3]["unmatched_visibility_names"],[])
        sampler=frames[0]["draws"][0]["textures"][0]["sampler"]
        self.assertEqual(sampler["gl_wrap_s"],0x8370);self.assertEqual(sampler["gl_wrap_t"],0x2901)
        self.assertEqual(sampler["gl_min_filter"],0x2702);self.assertEqual(sampler["gl_mag_filter"],0x2600)
        self.assertIn("generated",sampler["mipmap_origin"])
        print("PICA_GPU_TEST_RECEIPT",output/"pica-glb-render-receipt.json")


if __name__=="__main__":unittest.main(verbosity=2)
