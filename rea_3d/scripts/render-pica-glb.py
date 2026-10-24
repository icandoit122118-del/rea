"""Evaluate GLB TRS/skin on CPU and render material-state plans in private WGL.

No browser, visible application window, game, or input automation is used.
Plan inputs are read-only. New output directories are restricted to D: work.
"""
import argparse
import ctypes
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import struct
import sys
import time
import traceback
import zlib

sys.dont_write_bytecode = True
ROOT = Path("D:/REA3D-ORAS-20261023-1792751073625")
OUTPUT_ROOTS = (ROOT, Path("D:/REA3D-ORAS-MATERIAL-20261023-02"))
PACKAGES = ROOT / "gl-context-runtime-20261023" / "packages"
sys.path.insert(0, str(PACKAGES))
import numpy as np
import moderngl

_spec = importlib.util.spec_from_file_location("rea_gl_context", Path(__file__).with_name("gl-context-smoke.py"))
gl_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gl_helpers)

BUILTIN_VERTEX = """#version 330 core
in vec3 in_position; in vec3 in_normal; in vec2 in_uv0; in vec4 in_color;
uniform mat4 uMvp; uniform mat3 uUvMatrix0;
out vec2 vUv0; out vec3 vNormal; out vec4 vColor;
void main(){gl_Position=uMvp*vec4(in_position,1);vUv0=(uUvMatrix0*vec3(in_uv0,1)).xy;vNormal=in_normal;vColor=in_color;}
"""
BUILTIN_FRAGMENT = """#version 330 core
in vec2 vUv0; in vec3 vNormal; in vec4 vColor; uniform sampler2D uTexture0;
uniform vec4 uDiffuse; out vec4 frag_color;
void main(){vec4 c=texture(uTexture0,vUv0)*vColor*uDiffuse;if(c.a<0.05)discard;frag_color=c;}
"""
ATTRIBUTES = {"in_position": ("positions", 3), "in_normal": ("normals", 3), "in_tangent": ("tangents", 3),
              "in_uv0": ("uv0", 2), "in_uv1": ("uv1", 2), "in_uv2": ("uv2", 2), "in_color": ("colors", 4)}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_png_output(path,width,height,rgba):
    target=Path(path).resolve(strict=False)
    if not any(target.is_relative_to(root.resolve()) for root in OUTPUT_ROOTS):raise ValueError("PNG output outside authorized D: roots")
    data=gl_helpers.png_rgba_bytes(width,height,rgba)
    with target.open("xb") as stream:stream.write(data)
    return {"path":str(target),"bytes":len(data),"sha256":digest(data)}


def read_input(raw, max_bytes=256*1024*1024):
    path = Path(raw)
    if not path.is_absolute():
        raise ValueError("Explicit absolute input paths are required")
    path = path.resolve(strict=True)
    if not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError("Input is not a bounded regular file: " + str(path))
    return path, path.read_bytes()


def normalize(values):
    values = np.asarray(values, dtype=np.float64)
    length = np.linalg.norm(values, axis=-1, keepdims=True)
    return values / np.maximum(length, 1e-30)


def quaternion_slerp(a, b, amount):
    a, b = normalize(a), normalize(b)
    dot = float(np.dot(a, b))
    if dot < 0:
        b, dot = -b, -dot
    if dot > .9995:
        return normalize(a + amount * (b-a))
    theta = math.acos(max(-1.0, min(1.0, dot)))
    return (math.sin((1-amount)*theta)*a + math.sin(amount*theta)*b) / math.sin(theta)


def trs_matrix(translation, rotation, scale):
    x, y, z, w = normalize(rotation)
    rotation_matrix = np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                                [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                                [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])
    matrix = np.eye(4)
    matrix[:3, :3] = rotation_matrix @ np.diag(scale)
    matrix[:3, 3] = translation
    return matrix


def decode_png(data):
    """Decode bounded non-interlaced RGB/RGBA8 PNGs, preserving first PNG row."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Embedded image is not PNG")
    offset, compressed, header = 8, bytearray(), None
    while offset < len(data):
        if offset+12 > len(data):
            raise ValueError("Truncated PNG chunk")
        size = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset+4:offset+8]
        payload = data[offset+8:offset+8+size]
        if offset+size+12 > len(data) or zlib.crc32(kind+payload) & 0xffffffff != struct.unpack_from(">I",data,offset+8+size)[0]:
            raise ValueError("Invalid PNG chunk length or CRC")
        offset += size+12
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", payload)
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind == b"IEND":
            break
    if header is None:
        raise ValueError("PNG header missing")
    width, height, depth, color, method, filtering, interlace = header
    if depth != 8 or color not in (2,6) or method or filtering or interlace or width < 1 or height < 1 or width*height > 16*1024*1024:
        raise ValueError("Only bounded non-interlaced RGB/RGBA8 PNGs are supported")
    channels = 4 if color == 6 else 3
    stride, expected = width*channels, (width*channels+1)*height
    decoder = zlib.decompressobj()
    raw = decoder.decompress(bytes(compressed), expected+1)
    if len(raw) != expected or not decoder.eof:
        raise ValueError("PNG decompressed length mismatch")
    result = bytearray(width*height*4)
    previous = bytearray(stride)
    for y in range(height):
        start = y*(stride+1)
        mode = raw[start]
        row = bytearray(raw[start+1:start+1+stride])
        if mode > 4:
            raise ValueError("Invalid PNG row filter")
        for x in range(stride):
            left = row[x-channels] if x >= channels else 0
            up = previous[x]
            upper_left = previous[x-channels] if x >= channels else 0
            if mode == 1:
                predictor = left
            elif mode == 2:
                predictor = up
            elif mode == 3:
                predictor = (left+up)//2
            elif mode == 4:
                p=left+up-upper_left
                choices=(abs(p-left),abs(p-up),abs(p-upper_left))
                predictor = (left,up,upper_left)[choices.index(min(choices))]
            else:
                predictor = 0
            row[x] = (row[x]+predictor)&255
        if channels == 4:
            result[y*width*4:(y+1)*width*4] = row
        else:
            for x in range(width):
                result[(y*width+x)*4:(y*width+x+1)*4] = row[x*3:x*3+3]+b"\xff"
        previous = row
    return width, height, bytes(result)


class Glb:
    def __init__(self, raw_path):
        self.path, data = read_input(raw_path)
        self.sha256 = digest(data)
        if len(data)<20 or struct.unpack_from("<3I",data) != (0x46546c67,2,len(data)):
            raise ValueError("Invalid GLB v2 header")
        self.document, self.binary = None, b""
        offset=12
        while offset<len(data):
            size, kind = struct.unpack_from("<2I",data,offset); offset+=8
            if size%4 or offset+size>len(data): raise ValueError("Invalid GLB chunk")
            chunk=data[offset:offset+size];offset+=size
            if kind==0x4e4f534a: self.document=json.loads(chunk.decode())
            elif kind==0x004e4942: self.binary=chunk
        if not self.document or self.document.get("asset",{}).get("version") != "2.0":
            raise ValueError("Missing glTF 2.0 JSON")
        self.cache={}
        self.nodes=self.document.get("nodes",[])
        self.parents={}
        for parent,node in enumerate(self.nodes):
            for child in node.get("children",[]):
                if child in self.parents: raise ValueError("Node has multiple parents")
                self.parents[child]=parent
        self.primitives=[]
        for node_index,node in enumerate(self.nodes):
            if "mesh" not in node: continue
            mesh=self.document["meshes"][node["mesh"]]
            for primitive_index,primitive in enumerate(mesh["primitives"]):
                if primitive.get("mode",4)!=4: raise ValueError("Only triangle primitives are supported")
                attributes=primitive["attributes"]
                positions=self.accessor(attributes["POSITION"]).astype(np.float64)
                count=len(positions)
                normals=self.accessor(attributes["NORMAL"]).astype(np.float64) if "NORMAL" in attributes else np.tile([0,1,0],(count,1))
                tangent_source="_PICA_TANGENT" if "_PICA_TANGENT" in attributes else ("TANGENT" if "TANGENT" in attributes else "generated_perpendicular")
                tangents=self.accessor(attributes[tangent_source])[:,:3].astype(np.float64) if tangent_source in attributes else normalize(np.cross(normals,np.where((np.abs(normals[:,2])>.9)[:,None],np.array([1,0,0]),np.array([0,0,1]))))
                uv0=self.accessor(attributes["TEXCOORD_0"]).astype(np.float32) if "TEXCOORD_0" in attributes else np.zeros((count,2),np.float32)
                colors=self.accessor(attributes["COLOR_0"]).astype(np.float32) if "COLOR_0" in attributes else np.ones((count,4),np.float32)
                if colors.shape[1]==3: colors=np.column_stack([colors,np.ones(count)])
                indices=self.accessor(primitive["indices"]).reshape(-1).astype(np.uint32) if "indices" in primitive else np.arange(count,dtype=np.uint32)
                if len(indices)%3 or (len(indices) and int(indices.max())>=count): raise ValueError("Invalid triangle indices")
                entry={"node":node_index,"name":node.get("name",str(node_index)),"source_node":node.get("extras",{}).get("reaPicaNodeName",node.get("name",str(node_index))),
                       "primitive_index":primitive_index,"material":primitive.get("material"),"positions":positions,"normals":normals,"tangents":tangents,"uv0":uv0,
                       "uv1":self.accessor(attributes["TEXCOORD_1"]).astype(np.float32) if "TEXCOORD_1" in attributes else uv0,
                       "uv2":self.accessor(attributes["TEXCOORD_2"]).astype(np.float32) if "TEXCOORD_2" in attributes else uv0,
                       "colors":colors,"indices":indices,"skin":node.get("skin"),"tangent_source":tangent_source,
                       "uv_fallbacks":[n for n in (1,2) if "TEXCOORD_"+str(n) not in attributes]}
                if entry["skin"] is not None:
                    if "JOINTS_0" not in attributes or "WEIGHTS_0" not in attributes: raise ValueError("Skin requires JOINTS_0/WEIGHTS_0")
                    entry["joints"]=self.accessor(attributes["JOINTS_0"]).astype(np.int64)
                    entry["weights"]=self.accessor(attributes["WEIGHTS_0"]).astype(np.float64)
                self.primitives.append(entry)

    def view(self,index):
        view=self.document["bufferViews"][index]
        if view.get("buffer",0)!=0: raise ValueError("Only embedded GLB buffer 0 is supported")
        start=view.get("byteOffset",0);end=start+view["byteLength"]
        if start<0 or end>len(self.binary): raise ValueError("bufferView outside GLB binary")
        return self.binary[start:end]

    def accessor(self,index):
        if index in self.cache:return self.cache[index]
        accessor=self.document["accessors"][index]
        if "sparse" in accessor or "bufferView" not in accessor: raise ValueError("Sparse/missing accessor buffers are unsupported")
        components={"SCALAR":1,"VEC2":2,"VEC3":3,"VEC4":4,"MAT3":9,"MAT4":16}[accessor["type"]]
        dtype=np.dtype({5120:"i1",5121:"u1",5122:"<i2",5123:"<u2",5125:"<u4",5126:"<f4"}[accessor["componentType"]])
        view=self.document["bufferViews"][accessor["bufferView"]]
        data=self.view(accessor["bufferView"])
        offset=accessor.get("byteOffset",0);count=accessor["count"];stride=view.get("byteStride",components*dtype.itemsize)
        needed=offset+(count-1)*stride+components*dtype.itemsize if count else offset
        if offset<0 or stride<components*dtype.itemsize or needed>len(data):raise ValueError("Accessor exceeds bufferView")
        values=np.ndarray((count,components),dtype=dtype,buffer=data,offset=offset,strides=(stride,dtype.itemsize)).copy()
        if accessor.get("normalized") and dtype.kind in "iu":
            maximum=np.iinfo(dtype).max
            values=values.astype(np.float64)/maximum
            if dtype.kind=="i":values=np.maximum(values,-1)
        self.cache[index]=values
        return values

    def evaluate(self,frame):
        translations=[np.array(node.get("translation",[0,0,0]),dtype=float) for node in self.nodes]
        rotations=[np.array(node.get("rotation",[0,0,0,1]),dtype=float) for node in self.nodes]
        scales=[np.array(node.get("scale",[1,1,1]),dtype=float) for node in self.nodes]
        clip_index=frame.get("clip_index")
        seconds=float(frame.get("pose_time_seconds",frame.get("time_seconds",0)))
        if not math.isfinite(seconds):raise ValueError("Frame time must be finite")
        if clip_index is not None:
            animations=self.document.get("animations",[])
            if not isinstance(clip_index,int) or clip_index<0 or clip_index>=len(animations):raise ValueError("Invalid source GLB clip_index")
            animation=animations[clip_index]
            for channel in animation.get("channels",[]):
                target=channel["target"];node=target.get("node");path=target["path"]
                if path not in ("translation","rotation","scale") or node is None:raise ValueError("Only node TRS animation channels are supported")
                if "matrix" in self.nodes[node]:raise ValueError("Animated matrix nodes are unsupported")
                sampler=animation["samplers"][channel["sampler"]]
                times=self.accessor(sampler["input"]).reshape(-1)
                values=self.accessor(sampler["output"])
                if len(times)==0 or len(times)!=len(values) or np.any(np.diff(times)<0):raise ValueError("Invalid TRS animation samples")
                interpolation=sampler.get("interpolation","LINEAR")
                if interpolation not in ("LINEAR","STEP"):raise ValueError("Unsupported TRS interpolation: "+interpolation)
                if seconds<=times[0]:value=values[0]
                elif seconds>=times[-1]:value=values[-1]
                else:
                    right=int(np.searchsorted(times,seconds,side="right"));left=right-1
                    amount=float((seconds-times[left])/(times[right]-times[left]))
                    value=values[left] if interpolation=="STEP" else (quaternion_slerp(values[left],values[right],amount) if path=="rotation" else values[left]*(1-amount)+values[right]*amount)
                {"translation":translations,"rotation":rotations,"scale":scales}[path][node]=np.array(value)
        local=[np.array(node["matrix"],dtype=float).reshape((4,4),order="F") if "matrix" in node else trs_matrix(translations[index],rotations[index],scales[index]) for index,node in enumerate(self.nodes)]
        world={};visiting=set()
        def resolve(index):
            if index in world:return world[index]
            if index in visiting:raise ValueError("Node hierarchy cycle")
            visiting.add(index)
            world[index]=resolve(self.parents[index])@local[index] if index in self.parents else local[index]
            visiting.remove(index)
            return world[index]
        for index in range(len(self.nodes)):resolve(index)
        results=[]
        for primitive in self.primitives:
            count=len(primitive["positions"])
            if primitive["skin"] is not None:
                skin=self.document["skins"][primitive["skin"]]
                joints=skin["joints"]
                inverse_bind=self.accessor(skin["inverseBindMatrices"]).reshape((-1,4,4)).transpose(0,2,1) if "inverseBindMatrices" in skin else np.tile(np.eye(4),(len(joints),1,1))
                transforms=np.array([world[joint]@inverse_bind[index] for index,joint in enumerate(joints)])
                indices=primitive["joints"];weights=primitive["weights"]
                if np.any(indices<0) or np.any(indices>=len(joints)):raise ValueError("Vertex joint index outside skin")
                total=weights.sum(axis=1,keepdims=True)
                if np.any(total<=0):raise ValueError("Skin vertex has zero total weight")
                weights=weights/total
                blended=np.sum(transforms[indices]*weights[:,:,None,None],axis=1)
            else:
                blended=np.tile(world[primitive["node"]],(count,1,1))
            homogeneous=np.column_stack([primitive["positions"],np.ones(count)])
            positions=np.einsum("nij,nj->ni",blended,homogeneous)[:,:3]
            linear=blended[:,:3,:3]
            normals=normalize(np.einsum("nij,nj->ni",np.linalg.pinv(linear).transpose(0,2,1),primitive["normals"]))
            tangents=normalize(np.einsum("nij,nj->ni",linear,primitive["tangents"]))
            if not np.all(np.isfinite(positions)):raise ValueError("Non-finite skinned geometry")
            results.append({**primitive,"positions":positions.astype(np.float32),"normals":normals.astype(np.float32),"tangents":tangents.astype(np.float32)})
        return results,world


def visibility_for(primitive,visibility):
    value=visibility.get(primitive["source_node"],visibility.get(primitive["name"],True))
    if not isinstance(value,bool):raise ValueError("Visibility states must be booleans")
    return value


def bounds(primitives):
    if not primitives:raise ValueError("No visible geometry to frame")
    values=np.concatenate([primitive["positions"][np.unique(primitive["indices"])] for primitive in primitives])
    return {"min":values.min(axis=0).tolist(),"max":values.max(axis=0).tolist()}


def camera_from_bounds(box,width,height):
    low,high=np.array(box["min"]),np.array(box["max"])
    center=(low+high)/2;radius=max(float(np.linalg.norm(high-low)/2),.01)
    fov=math.radians(35);aspect=width/height
    effective=min(fov,2*math.atan(math.tan(fov/2)*aspect))
    distance=radius/math.sin(effective/2)*1.10
    eye=center+normalize([1.4,.9,2.8])*distance
    forward=normalize(center-eye);right=normalize(np.cross(forward,[0,1,0]));up=np.cross(right,forward)
    view=np.eye(4);view[:3,:3]=np.array([right,up,-forward]);view[:3,3]=-view[:3,:3]@eye
    near=max(radius/1000,.0001);far=distance+radius*100
    projection=np.zeros((4,4));factor=1/math.tan(fov/2)
    projection[0,0]=factor/aspect;projection[1,1]=factor;projection[2,2]=(far+near)/(near-far);projection[2,3]=2*far*near/(near-far);projection[3,2]=-1
    return projection@view,{"position":eye.tolist(),"target":center.tolist(),"up":"Y","front":"+Z","fov_degrees":35,"bounds_union_visible_posed":box,"near":near,"far":far}


def color_value(value,default):
    if value is None:return default
    if isinstance(value,dict):value=[value.get(key,default[index]) for index,key in enumerate(("r","g","b","a"))]
    if not isinstance(value,(list,tuple)) or len(value) not in (3,4):raise ValueError("Color must be an RGB/RGBA array")
    result=[float(v) for v in value]
    if len(result)==3:result.append(1.)
    if not all(math.isfinite(v) for v in result):raise ValueError("Non-finite color")
    return result


def apply_uniform(program,name,value,applied,matrix=False):
    if name not in program:return
    if matrix:
        data=np.asarray(value,dtype="<f4")
        program[name].write(data.tobytes())
        applied[name]=data.reshape(-1).tolist()
    else:
        program[name].value=tuple(value) if isinstance(value,(list,tuple,np.ndarray)) else value
        applied[name]=value.tolist() if isinstance(value,np.ndarray) else value


def apply_render_state(ctx,framebuffer,config,gltf_material):
    depth=bool(config.get("depth_test",True));write=bool(config.get("depth_write",True))
    ctx.enable(moderngl.DEPTH_TEST) if depth else ctx.disable(moderngl.DEPTH_TEST)
    depth_func=config.get("depth_func","<=")
    depth_func={"Less":"<","LessOrEqual":"<=","Lequal":"<=","LEqual":"<=","Always":"1","Never":"0","Greater":">","GreaterOrEqual":">=","Gequal":">=","GEqual":">=","Equal":"==","NotEqual":"!=","Notequal":"!="}.get(depth_func,depth_func)
    if depth_func not in ("<","<=",">",">=","==","!=","1","0"):raise ValueError("Unsupported depth comparison: "+str(depth_func))
    ctx.depth_func=depth_func;framebuffer.depth_mask=write
    cull=config.get("cull_mode","none" if gltf_material.get("doubleSided",False) else "back")
    cull=str(cull).lower()
    if cull in ("none","never","disabled"):ctx.disable(moderngl.CULL_FACE)
    else:
        ctx.enable(moderngl.CULL_FACE);ctx.cull_face="front" if cull=="front" else "back"
    ctx.front_face=config.get("front_face","ccw")
    blend=config.get("blend",{})
    if isinstance(blend,bool):blend={"enabled":blend}
    enabled=bool(blend.get("enabled",gltf_material.get("alphaMode")=="BLEND"))
    if enabled:
        ctx.enable(moderngl.BLEND)
        factors={"Zero":moderngl.ZERO,"One":moderngl.ONE,"SourceAlpha":moderngl.SRC_ALPHA,"OneMinusSourceAlpha":moderngl.ONE_MINUS_SRC_ALPHA,"DestinationAlpha":moderngl.DST_ALPHA,"OneMinusDestinationAlpha":moderngl.ONE_MINUS_DST_ALPHA,"SourceColor":moderngl.SRC_COLOR,"OneMinusSourceColor":moderngl.ONE_MINUS_SRC_COLOR,"DestinationColor":moderngl.DST_COLOR,"OneMinusDestinationColor":moderngl.ONE_MINUS_DST_COLOR,"ConstantColor":0x8001,"OneMinusConstantColor":0x8002,"ConstantAlpha":0x8003,"OneMinusConstantAlpha":0x8004,"SourceAlphaSaturate":0x0308}
        def factor(key,default):
            value=blend.get(key,default)
            if value not in factors:raise ValueError("Unsupported blend factor: "+str(value))
            return factors[value]
        ctx.blend_func=(factor("src_rgb","SourceAlpha"),factor("dst_rgb","OneMinusSourceAlpha"),factor("src_alpha","One"),factor("dst_alpha","OneMinusSourceAlpha"))
        equations={"Add":moderngl.FUNC_ADD,"FuncAdd":moderngl.FUNC_ADD,"Subtract":moderngl.FUNC_SUBTRACT,"FuncSubtract":moderngl.FUNC_SUBTRACT,"ReverseSubtract":moderngl.FUNC_REVERSE_SUBTRACT,"FuncReverseSubtract":moderngl.FUNC_REVERSE_SUBTRACT,"Min":moderngl.MIN,"Max":moderngl.MAX}
        ctx.blend_equation=(equations[blend.get("equation_rgb","Add")],equations[blend.get("equation_alpha","Add")])
    else:ctx.disable(moderngl.BLEND)
    return {"depth_test":depth,"depth_write":write,"depth_func":depth_func,"cull_mode":cull,"blend_enabled":enabled,"blend":blend}


def gl_function(name,result,*arguments):
    library=ctypes.WinDLL("opengl32.dll")
    resolver=library.wglGetProcAddress;resolver.argtypes=[ctypes.c_char_p];resolver.restype=ctypes.c_void_p
    address=resolver(name.encode("ascii"))
    if not address or address in (1,2,3,ctypes.c_void_p(-1).value):raise RuntimeError("Driver does not expose "+name)
    return ctypes.WINFUNCTYPE(result,*arguments)(address)


def material_sampler(ctx,texture,mapper,mipmap_images,image_name):
    # PICA filter enums are ordered differently from OpenGL filter enums.
    min_filters={0:0x2600,1:0x2700,2:0x2702,3:0x2601,4:0x2701,5:0x2703}
    mag_filters={0:0x2600,1:0x2601}
    wrap_modes={0:0x812f,1:0x812d,2:0x2901,3:0x8370}
    min_code=int(mapper.get("min_filter",3));mag_code=int(mapper.get("mag_filter",1))
    wrap_u=int(mapper.get("wrap_u",0));wrap_v=int(mapper.get("wrap_v",0))
    if min_code not in min_filters or mag_code not in mag_filters or wrap_u not in wrap_modes or wrap_v not in wrap_modes:raise ValueError("Unknown native texture mapper enum")
    needs_mips=min_code in (1,2,4,5)
    if needs_mips and image_name not in mipmap_images:
        texture.build_mipmaps();mipmap_images.add(image_name)
    sampler=ctx.sampler(texture=texture,filter=(min_filters[min_code],mag_filters[mag_code]),repeat_x=wrap_u==2,repeat_y=wrap_v==2,border_color=tuple(mapper.get("border_color",[0,0,0,1])),min_lod=float(mapper.get("min_lod",0)))
    set_integer=gl_function("glSamplerParameteri",None,ctypes.c_uint,ctypes.c_uint,ctypes.c_int)
    set_float=gl_function("glSamplerParameterf",None,ctypes.c_uint,ctypes.c_uint,ctypes.c_float)
    # ModernGL's repeat flags are boolean. Apply exact GL repeat/mirror/border
    # state to this private per-material/unit sampler through the same driver.
    set_integer(sampler._glo,0x2802,wrap_modes[wrap_u]);set_integer(sampler._glo,0x2803,wrap_modes[wrap_v])
    set_float(sampler._glo,0x8501,float(mapper.get("lod_bias",0)))
    get_integer=gl_function("glGetSamplerParameteriv",None,ctypes.c_uint,ctypes.c_uint,ctypes.POINTER(ctypes.c_int))
    actual={}
    for name,pname,expected in (("min_filter",0x2801,min_filters[min_code]),("mag_filter",0x2800,mag_filters[mag_code]),("wrap_s",0x2802,wrap_modes[wrap_u]),("wrap_t",0x2803,wrap_modes[wrap_v])):
        value=ctypes.c_int();get_integer(sampler._glo,pname,ctypes.byref(value));actual[name]=value.value
        if value.value!=expected:raise RuntimeError("Driver sampler readback mismatch: "+name)
    state={"native_mapper":mapper,"gl_min_filter":min_filters[min_code],"gl_mag_filter":mag_filters[mag_code],"gl_wrap_s":wrap_modes[wrap_u],"gl_wrap_t":wrap_modes[wrap_v],"gl_sampler_state_readback":actual,"sampler_scope":"private per material/unit/image","mipmap_origin":"generated on GPU from embedded PNG level 0; original source mip chain not supplied" if needs_mips else "level 0 only","mirror_implementation":"GL_MIRRORED_REPEAT driver sampler" if 3 in (wrap_u,wrap_v) else None}
    return sampler,state


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan",required=True);parser.add_argument("--output-dir",required=True)
    options=parser.parse_args()
    plan_path,plan_bytes=read_input(options.plan,16*1024*1024);plan=json.loads(plan_bytes.decode("utf-8-sig"))
    output=Path(options.output_dir)
    if not output.is_absolute():raise ValueError("Output must be absolute")
    output=output.resolve(strict=False)
    if not any(output!=root.resolve() and output.is_relative_to(root.resolve()) for root in OUTPUT_ROOTS):raise ValueError("Output must stay within authorized D: roots")
    output.mkdir(parents=True,exist_ok=False)
    (output/"tmp").mkdir()
    for key in ("TEMP","TMP","TMPDIR","XDG_CACHE_HOME"):os.environ[key]=str(output/"tmp")
    receipt={"schema":"rea3d-pica-glb-gpu-v1","ok":False,"execution":"numpy_gltf_trs_skin_to_actual_opengl_glsl_framebuffer",
             "plan":{"path":str(plan_path),"sha256":digest(plan_bytes)},"frames":[],"limitations":["Native PICA shader/state translation is supplied by the plan; original-game visual parity is not asserted.","CPU skin supports JOINTS_0/WEIGHTS_0 and node TRS LINEAR/STEP with quaternion SLERP; morph/CUBICSPLINE are rejected.","GLB PNG first row is uploaded unchanged; no second UV/image V inversion is performed.","Missing UV1/UV2 fall back to UV0 and missing normals use (0,1,0). Tangent handedness is not represented by the requested vec3 interface."]}
    ctx=None;resources=[];started=time.time()
    try:
        width=int(plan.get("width",640));height=int(plan.get("height",640))
        if not 64<=width<=2048 or not 64<=height<=2048:raise ValueError("Resolution outside 64..2048")
        frames=plan.get("frames",[])
        if not isinstance(frames,list) or not 1<=len(frames)<=128:raise ValueError("Plan needs 1..128 frames")
        model=Glb(plan["glb"])
        receipt["glb"]={"path":str(model.path),"sha256_before":model.sha256,"meshes":len(model.primitives),"vertices":sum(len(p["positions"]) for p in model.primitives),"triangles":sum(len(p["indices"])//3 for p in model.primitives),"clips":len(model.document.get("animations",[]))}
        evaluated=[];visible_all=[]
        for frame in frames:
            primitives,world=model.evaluate(frame)
            visibility=frame.get("visibility",{})
            model_visible=frame.get("model_visible",True)
            if not isinstance(model_visible,bool):raise ValueError("model_visible must be boolean")
            visible=[p for p in primitives if model_visible and visibility_for(p,visibility)]
            evaluated.append((primitives,world,visible));visible_all.extend(visible)
        camera_bounds=plan["camera_bounds"] if "camera_bounds" in plan else bounds(visible_all)
        for key in ("min","max"):
            if not isinstance(camera_bounds.get(key),list) or len(camera_bounds[key])!=3 or not all(isinstance(value,(int,float)) and math.isfinite(value) for value in camera_bounds[key]):raise ValueError("camera_bounds needs finite min/max XYZ arrays")
        if any(low>high for low,high in zip(camera_bounds["min"],camera_bounds["max"])):raise ValueError("camera_bounds min exceeds max")
        mvp,camera=camera_from_bounds(camera_bounds,width,height);camera["fit_source"]="explicit plan.camera_bounds" if "camera_bounds" in plan else "all visible posed geometry";receipt["camera"]=camera
        ctx=gl_helpers.create_gpu_context();receipt["context"]=gl_helpers.context_information(ctx)
        color=ctx.texture((width,height),4,dtype="f1");depth=ctx.depth_renderbuffer((width,height));fbo=ctx.framebuffer([color],depth)
        resources.extend([color,depth,fbo])
        textures={};image_receipts=[]
        for index,image in enumerate(model.document.get("images",[])):
            if "bufferView" not in image or image.get("mimeType")!="image/png":raise ValueError("Only embedded PNG images are supported")
            png=model.view(image["bufferView"]);iw,ih,rgba=decode_png(png)
            texture=ctx.texture((iw,ih),4,rgba,alignment=1);texture.filter=(moderngl.LINEAR,moderngl.LINEAR);texture.repeat_x=False;texture.repeat_y=False
            resources.append(texture);name=image.get("name","image_"+str(index))
            if name in textures:raise ValueError("Duplicate image name")
            textures[name]=texture
            image_receipts.append({"index":index,"name":name,"width":iw,"height":ih,"png_sha256":digest(png),"rgba_sha256":digest(rgba),"row_convention":"PNG first row uploaded as GL texture row 0, unchanged"})
        white=ctx.texture((1,1),4,b"\xff"*4);resources.append(white);textures["__white__"]=white;receipt["images"]=image_receipts
        materials=model.document.get("materials",[]);programs={};shader_receipts=[];shader_inputs={}
        for material_index,material in enumerate(materials):
            name=material.get("name","material_"+str(material_index));entry=plan.get("materials",{}).get(name,{})
            if entry.get("vertex") and entry.get("fragment"):
                vp,vb=read_input(entry["vertex"],1024*1024);fp,fb=read_input(entry["fragment"],1024*1024)
                vertex,fragment=vb.decode("utf-8-sig"),fb.decode("utf-8-sig");shader_inputs[str(vp)]=digest(vb);shader_inputs[str(fp)]=digest(fb);origin="plan_shader_files"
            else:vertex,fragment,origin=BUILTIN_VERTEX,BUILTIN_FRAGMENT,"builtin_diagnostic_textured_shader"
            program=ctx.program(vertex_shader=vertex,fragment_shader=fragment);resources.append(program)
            programs[material_index]=(program,entry.get("config",{}))
            shader_receipts.append({"material":name,"origin":origin,"vertex_sha256":digest(vertex.encode()),"fragment_sha256":digest(fragment.encode()),"active_members":list(program)})
        receipt["compiled_shaders"]=shader_receipts;receipt["shader_inputs_sha256_before"]=shader_inputs
        reference_points=None;reference_bones=None;reference_pixels=None;sampler_cache={};mipmap_images=set()
        for frame_index,(frame,(primitives,world,visible)) in enumerate(zip(frames,evaluated)):
            fbo.use();ctx.viewport=(0,0,width,height);fbo.depth_mask=True;fbo.color_mask=(True,True,True,True);fbo.clear(.12,.15,.20,1,depth=1)
            frame_receipt={"index":frame_index,"clip_index":frame.get("clip_index"),"time_seconds":float(frame.get("time_seconds",0)),"pose_time_seconds":float(frame.get("pose_time_seconds",frame.get("time_seconds",0))),"source_frame":frame.get("frame"),"visible_meshes":[{"name":p["name"],"source_node":p["source_node"],"primitive":p["primitive_index"]} for p in visible],"visibility_requested":frame.get("visibility",{}),"draws":[]}
            known_names={p["source_node"] for p in primitives}|{p["name"] for p in primitives}
            frame_receipt["unmatched_visibility_names"]=sorted(set(frame.get("visibility",{}))-known_names)
            points=np.concatenate([p["positions"][np.unique(p["indices"])] for p in primitives]);bone_matrices=np.array([world[index] for skin in model.document.get("skins",[]) for index in skin["joints"]])
            if reference_points is None:reference_points=points.copy();reference_bones=bone_matrices.copy()
            displacement=np.linalg.norm(points-reference_points,axis=1)
            bone_delta=np.max(np.abs(bone_matrices-reference_bones),axis=(1,2)) if len(bone_matrices) else np.array([])
            frame_receipt["geometry"]={"evaluated_vertices":len(points),"evaluation_vertex_scope":"Unique referenced indices per primitive; unused accessor vertices excluded from bounds and deformation comparisons","world_positions_sha256":digest(points.astype("<f4").tobytes()),"bounds_all":bounds(primitives),"bounds_visible":bounds(visible) if visible else None,"moved_vertices_from_first_frame":int(np.count_nonzero(displacement>1e-6)),"max_vertex_displacement_from_first_frame":float(displacement.max(initial=0)),"changed_joint_matrices_from_first_frame":int(np.count_nonzero(bone_delta>1e-6)),"max_joint_matrix_delta_from_first_frame":float(bone_delta.max(initial=0)),"joint_matrices_sha256":digest(bone_matrices.astype("<f8").tobytes())}
            for primitive in visible:
                material_index=primitive["material"]
                if material_index not in programs:raise ValueError("Primitive has no compiled material")
                material=materials[material_index];name=material.get("name","material_"+str(material_index));program,config=programs[material_index]
                state=frame.get("material_states",{}).get(name,{})
                draw={"mesh":primitive["name"],"source_node":primitive["source_node"],"material":name,"vertices":len(primitive["positions"]),"triangles":len(primitive["indices"])//3,"material_state":state,"uv_fallbacks":primitive["uv_fallbacks"],"tangent_source":primitive["tangent_source"],"uniforms":{},"textures":[]}
                draw["render_state"]=apply_render_state(ctx,fbo,config,material)
                applied=draw["uniforms"]
                apply_uniform(program,"uMvp",mvp.T,applied,True)
                matrices=state.get("uv_matrices",config.get("uv_matrices",[np.eye(3).reshape(-1).tolist()]*3))
                for slot in range(3):apply_uniform(program,"uUvMatrix"+str(slot),matrices[slot] if slot<len(matrices) else np.eye(3).reshape(-1),applied,True)
                colors=state.get("colors",config.get("colors",{}))
                for key in ("emission","ambient","diffuse","specular0","specular1")+tuple("constant"+str(i) for i in range(6)):
                    default=[1,1,1,1] if key in ("diffuse","constant0") else [0,0,0,1]
                    apply_uniform(program,"u"+key[0].upper()+key[1:],color_value(colors.get(key),default),applied)
                lighting=plan.get("scene_lighting",{})
                for uniform,value in {"uBufferColor":color_value(state.get("buffer_color",config.get("buffer_color",config.get("texenv_buffer_color"))),[0,0,0,0]),"uSceneAmbient":plan.get("scene_ambient",lighting.get("scene_ambient",[.25,.25,.25])),"uLightDirection":normalize(plan.get("light_direction",lighting.get("light_direction",[.3,.8,.6]))).tolist(),"uLightColor":plan.get("light_color",lighting.get("light_color",[1,1,1])),"uCameraPosition":camera["position"]}.items():apply_uniform(program,uniform,value,applied)
                for uniform,value in {**config.get("uniforms",{}),**state.get("uniforms",{})}.items():apply_uniform(program,uniform,value,applied)
                base_index=material.get("pbrMetallicRoughness",{}).get("baseColorTexture",{}).get("index")
                base_name=model.document["images"][model.document["textures"][base_index]["source"]].get("name") if base_index is not None else "__white__"
                names=state.get("texture_names",config.get("texture_names",[base_name,"__white__","__white__"]))
                for slot in range(3):
                    texture_name=names[slot] if slot<len(names) and names[slot] else "__white__"
                    if texture_name not in textures:raise ValueError("Requested texture not embedded in GLB: "+str(texture_name))
                    texture=textures[texture_name]
                    mappers=state.get("texture_mappers",config.get("texture_mappers",[]));mapper=mappers[slot] if slot<len(mappers) else {}
                    sampler_key=(material_index,slot,texture_name,json.dumps(mapper,sort_keys=True))
                    if sampler_key not in sampler_cache:
                        sampler,sampler_state=material_sampler(ctx,texture,mapper,mipmap_images,texture_name);resources.append(sampler);sampler_cache[sampler_key]=(sampler,sampler_state)
                    sampler,sampler_state=sampler_cache[sampler_key];sampler.use(slot);apply_uniform(program,"uTexture"+str(slot),slot,applied)
                    draw["textures"].append({"slot":slot,"name":texture_name,"sampler":sampler_state})
                draw_resources=[]
                try:
                    content=[]
                    for attribute,(key,size) in ATTRIBUTES.items():
                        if attribute not in program:continue
                        buffer=ctx.buffer(np.ascontiguousarray(primitive[key],dtype="<f4").tobytes());draw_resources.append(buffer);content.append((buffer,str(size)+"f",attribute))
                    index_buffer=ctx.buffer(primitive["indices"].astype("<u4").tobytes());draw_resources.append(index_buffer)
                    vao=ctx.vertex_array(program,content,index_buffer=index_buffer,index_element_size=4);draw_resources.append(vao);vao.render(mode=moderngl.TRIANGLES)
                finally:
                    for resource in reversed(draw_resources):resource.release()
                frame_receipt["draws"].append(draw)
            ctx.finish();rgba=fbo.read(components=4,alignment=1,dtype="f1")
            error=ctx.error
            if error!="GL_NO_ERROR":raise RuntimeError("OpenGL frame error: "+error)
            frame_receipt["opengl_error"]=error;frame_receipt["rgba_sha256"]=digest(rgba)
            pixels=np.frombuffer(rgba,dtype=np.uint8).reshape((height,width,4))
            if reference_pixels is None:reference_pixels=pixels.copy()
            pixel_delta=np.abs(pixels.astype(np.int16)-reference_pixels.astype(np.int16))
            changed=np.any(pixel_delta!=0,axis=2);ys,xs=np.nonzero(changed)
            frame_receipt["pixel_difference_from_first_frame"]={"changed_pixels":int(np.count_nonzero(changed)),"max_channel_delta":int(pixel_delta.max(initial=0)),"changed_bbox_png_top_left":[int(xs.min()),int(height-1-ys.max()),int(xs.max()),int(height-1-ys.min())] if len(xs) else None}
            frame_receipt["png"]=write_png_output(output/("frame_%03d.png"%frame_index),width,height,rgba)
            receipt["frames"].append(frame_receipt)
        receipt["glb"]["sha256_after"]=digest(model.path.read_bytes());receipt["glb"]["unchanged"]=receipt["glb"]["sha256_after"]==model.sha256
        receipt["shader_inputs_sha256_after"]={path:digest(Path(path).read_bytes()) for path in shader_inputs}
        receipt["inputs_unchanged"]=receipt["glb"]["unchanged"] and receipt["shader_inputs_sha256_after"]==shader_inputs and digest(plan_path.read_bytes())==receipt["plan"]["sha256"]
        if not receipt["inputs_unchanged"]:raise ValueError("An input file changed during rendering")
        receipt["ok"]=True
        receipt["evidence"]={"actual_opengl_shader_compile_and_model_frame_readback":True,"actual_cpu_trs_and_weighted_skin":True,"original_game_visual_parity":False,"diagnostic_shader_used":any(entry["origin"].startswith("builtin") for entry in shader_receipts)}
    except Exception as error:
        receipt["error"]={"type":type(error).__name__,"message":str(error),"traceback":traceback.format_exc()}
    finally:
        for resource in reversed(resources):
            try:resource.release()
            except Exception:pass
        if ctx is not None:ctx.release()
        receipt["elapsed_seconds"]=time.time()-started
        receipt_path=output/"pica-glb-render-receipt.json"
        with receipt_path.open("x",encoding="utf-8") as stream:json.dump(receipt,stream,indent=2,ensure_ascii=False,allow_nan=False);stream.write("\n")
        print(json.dumps({"ok":receipt["ok"],"receipt":str(receipt_path),"frames":len(receipt["frames"]),"renderer":receipt.get("context",{}).get("renderer"),"error":receipt.get("error",{}).get("message")},ensure_ascii=False))
    if not receipt["ok"]:raise SystemExit(1)


if __name__=="__main__":main()
