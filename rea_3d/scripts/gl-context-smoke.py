"""GUI-free WGL/OpenGL shader compile, framebuffer draw, and readback receipt.

Use the existing portable Python with this script; dependencies remain in the
task-specific D: runtime. Optional --vertex/--fragment paths accept GLSL files
that use gl_VertexID for a fullscreen triangle, without vertex attributes.
The helpers can also be imported by a model-specific ModernGL renderer.
"""

import argparse
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import struct
import sys
import time
import traceback
import zlib


WORK_ROOT = Path("D:/REA3D-ORAS-20261023-1792751073625")
DEFAULT_MODULE_DIR = WORK_ROOT / "gl-context-runtime-20261023" / "packages"
VERTEX_SHADER = """#version 330 core
void main() {
    vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}
"""
FRAGMENT_SHADER = """#version 330 core
out vec4 frag_color;
void main() {
    ivec2 cell = ivec2(floor(gl_FragCoord.xy / 32.0));
    bool bright = ((cell.x + cell.y) % 2) == 1;
    vec3 color = bright ? vec3(0.95, 0.68, 0.15) : vec3(0.08, 0.30, 0.65);
    frag_color = vec4(color, 1.0);
}
"""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def load_runtime(module_dir=DEFAULT_MODULE_DIR):
    """Add only an explicitly selected isolated dependency directory to sys.path."""
    resolved = Path(module_dir).resolve(strict=True)
    if not resolved.is_dir() or not resolved.is_relative_to(WORK_ROOT.resolve(strict=True)):
        raise ValueError("Runtime dependencies must be in the task-specific D: root")
    if str(resolved) not in sys.path:
        sys.path.insert(0, str(resolved))
    import moderngl
    return moderngl


def create_gpu_context(module_dir=DEFAULT_MODULE_DIR, require=330, backend="default"):
    """Create a private standalone context. Windows default uses hidden WGL."""
    moderngl = load_runtime(module_dir)
    kwargs = {"require": require}
    if backend != "default":
        kwargs["backend"] = backend
    return moderngl.create_standalone_context(**kwargs)


def context_information(ctx):
    info = dict(ctx.info)
    glsl = info.get("GL_SHADING_LANGUAGE_VERSION")
    if platform.system() == "Windows":
        gl_get_string = ctypes.WinDLL("opengl32.dll").glGetString
        gl_get_string.argtypes = [ctypes.c_uint]
        gl_get_string.restype = ctypes.c_char_p
        value = gl_get_string(0x8B8C)
        glsl = value.decode("ascii", errors="replace") if value else None
    return {
        "version_code": ctx.version_code,
        "vendor": info.get("GL_VENDOR"),
        "renderer": info.get("GL_RENDERER"),
        "version": info.get("GL_VERSION"),
        "shading_language_version": glsl,
        "limits_and_info": info,
    }


def png_rgba_bytes(width, height, bottom_up_rgba):
    """Encode OpenGL bottom-up RGBA8 readback as a top-down standard PNG."""
    if len(bottom_up_rgba) != width * height * 4:
        raise ValueError("RGBA8 readback length does not match dimensions")

    def chunk(kind, data):
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    stride = width * 4
    scanlines = b"".join(b"\0" + bottom_up_rgba[row * stride:(row + 1) * stride] for row in range(height - 1, -1, -1))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(scanlines, 6)) + chunk(b"IEND", b"")


def write_png_rgba(path, width, height, bottom_up_rgba):
    """Write a new PNG only inside the authorized D: evidence root."""
    target = Path(path).resolve(strict=False)
    if not target.is_relative_to(WORK_ROOT.resolve(strict=True)):
        raise ValueError("PNG output must stay within the task-specific D: root")
    data = png_rgba_bytes(width, height, bottom_up_rgba)
    with target.open("xb") as stream:
        stream.write(data)
    return {"path": str(target), "bytes": len(data), "sha256": sha256(data)}


def render_fullscreen(ctx, vertex_shader, fragment_shader, width=256, height=256, uniforms=None):
    """Compile/link GLSL, draw one triangle into RGBA8, finish and read GPU bytes.

    A caller that needs geometry/skin buffers can reuse create_gpu_context() and
    write_png_rgba(), then use ctx.buffer/vertex_array/framebuffer directly.
    """
    import moderngl
    resources = []
    try:
        program = ctx.program(vertex_shader=vertex_shader, fragment_shader=fragment_shader)
        resources.append(program)
        applied = {}
        for name, value in {"u_resolution": (width, height), "u_time": 0.0}.items():
            if name in program:
                program[name].value = value
                applied[name] = value
        for name, value in (uniforms or {}).items():
            if not isinstance(name, str) or name not in program:
                raise ValueError("Requested uniform is not active: " + str(name))
            numbers = value if isinstance(value, (list, tuple)) else [value]
            if not all(isinstance(item, (int, float)) and math.isfinite(item) for item in numbers):
                raise ValueError("Uniforms must contain finite numeric scalar/vector values")
            program[name].value = tuple(value) if isinstance(value, list) else value
            applied[name] = value
        vao = ctx.vertex_array(program, [])
        resources.append(vao)
        color = ctx.texture((width, height), components=4, dtype="f1")
        resources.append(color)
        framebuffer = ctx.framebuffer(color_attachments=[color])
        resources.append(framebuffer)
        framebuffer.use()
        ctx.viewport = (0, 0, width, height)
        ctx.disable(moderngl.DEPTH_TEST | moderngl.CULL_FACE | moderngl.BLEND)
        framebuffer.clear(1, 0, 1, 1)
        vao.render(mode=moderngl.TRIANGLES, vertices=3)
        ctx.finish()
        data = framebuffer.read(components=4, alignment=1, dtype="f1")
        error = ctx.error
        if error != "GL_NO_ERROR":
            raise RuntimeError("OpenGL reported " + error)
        if len(data) != width * height * 4:
            raise RuntimeError("Unexpected framebuffer readback length")
        return data, {"shader_members": list(program), "uniforms_applied": applied, "opengl_error": error, "draw_calls": 1, "vertices": 3, "framebuffer": "RGBA8", "readback_bytes": len(data), "readback_sha256": sha256(data)}
    finally:
        for resource in reversed(resources):
            resource.release()


def shader_source(path, default):
    if path is None:
        return default, None
    selected = Path(path)
    if not selected.is_absolute():
        raise ValueError("Shader input paths must be absolute")
    selected = selected.resolve(strict=True)
    if not selected.is_file() or selected.stat().st_size > 1024 * 1024:
        raise ValueError("Shader input must be a regular file no larger than 1 MiB")
    return selected.read_text(encoding="utf-8-sig"), selected


def check_default_pattern(data, width, height):
    samples = []
    for x, y in ((16, 16), (48, 16), (16, 48), (48, 48)):
        if x >= width or y >= height:
            continue
        offset = (y * width + x) * 4
        actual = list(data[offset:offset + 4])
        bright = ((x // 32 + y // 32) % 2) == 1
        expected = [242, 173, 38, 255] if bright else [20, 77, 166, 255]
        error = max(abs(a - b) for a, b in zip(actual, expected))
        samples.append({"pixel_opengl_bottom_left": [x, y], "actual_rgba8": actual, "expected_rgba8_approximately": expected, "max_channel_error": error, "passed": error <= 2})
    return {"kind": "four known interior checker pixels from actual GPU framebuffer", "passed": bool(samples) and all(sample["passed"] for sample in samples), "samples": samples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, help="New evidence directory inside task-specific D: root")
    parser.add_argument("--module-dir", default=str(DEFAULT_MODULE_DIR))
    parser.add_argument("--vertex", help="Optional fullscreen gl_VertexID vertex shader file")
    parser.add_argument("--fragment", help="Optional fragment shader file")
    parser.add_argument("--uniforms-file", help="Optional JSON numeric uniform map")
    parser.add_argument("--size", type=int, default=256)
    parser.add_argument("--require", type=int, default=330)
    parser.add_argument("--backend", choices=("default", "egl"), default="default")
    opts = parser.parse_args()
    if not 64 <= opts.size <= 2048:
        raise ValueError("Size must be between 64 and 2048")
    if not 330 <= opts.require <= 460:
        raise ValueError("Required OpenGL version must be between 330 and 460")
    output = Path(opts.output_dir)
    if not output.is_absolute():
        raise ValueError("Output must be an absolute new directory")
    output = output.resolve(strict=False)
    root = WORK_ROOT.resolve(strict=True)
    if output == root or not output.is_relative_to(root):
        raise ValueError("Evidence outputs must stay within the task-specific D: root")
    output.mkdir(parents=True, exist_ok=False)
    temporary = output / "tmp"
    temporary.mkdir()
    for key in ("TEMP", "TMP", "TMPDIR", "XDG_CACHE_HOME"):
        os.environ[key] = str(temporary)
    receipt = {
        "schema": "rea3d-opengl-context-smoke-v1", "ok": False,
        "execution": "standalone_opengl_compile_draw_finish_and_framebuffer_readback",
        "python": {"version": sys.version, "executable": sys.executable, "platform": platform.platform()},
        "module_directory": str(Path(opts.module_dir).resolve(strict=False)),
        "context_backend": "Windows WGL standalone (no visible UI or input)" if opts.backend == "default" and platform.system() == "Windows" else opts.backend,
        "require_version_code": opts.require,
        "evidence": {"context_created": False, "shader_compile_link_and_draw_readback": False, "png_written": False,
                     "original_game_model_rendered": False, "original_game_shader_parity": False},
    }
    started = time.time()
    ctx = None
    source_paths = []
    try:
        vertex, vertex_path = shader_source(opts.vertex, VERTEX_SHADER)
        fragment, fragment_path = shader_source(opts.fragment, FRAGMENT_SHADER)
        source_paths = [path for path in (vertex_path, fragment_path) if path is not None]
        hashes_before = {str(path): sha256(path.read_bytes()) for path in source_paths}
        for filename, source in (("fullscreen.vert.glsl", vertex), ("fragment.frag.glsl", fragment)):
            with (output / filename).open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(source)
        receipt["shaders"] = {"vertex_sha256": sha256(vertex.encode("utf-8")), "fragment_sha256": sha256(fragment.encode("utf-8")), "input_sha256_before": hashes_before, "default_checker": opts.vertex is None and opts.fragment is None}
        uniforms = {}
        if opts.uniforms_file:
            uniforms_path = Path(opts.uniforms_file).resolve(strict=True)
            if uniforms_path.stat().st_size > 1024 * 1024:
                raise ValueError("Uniform JSON is too large")
            uniforms = json.loads(uniforms_path.read_text(encoding="utf-8-sig"))
            if not isinstance(uniforms, dict):
                raise ValueError("Uniform JSON must contain an object")
        moderngl = load_runtime(opts.module_dir)
        import glcontext
        receipt["dependencies"] = {"moderngl": moderngl.__version__, "glcontext": glcontext.__version__}
        try:
            import numpy
            receipt["dependencies"]["numpy"] = numpy.__version__
        except ImportError:
            receipt["dependencies"]["numpy"] = None
        ctx = create_gpu_context(opts.module_dir, opts.require, opts.backend)
        receipt["evidence"]["context_created"] = True
        receipt["context"] = context_information(ctx)
        data, rendered = render_fullscreen(ctx, vertex, fragment, opts.size, opts.size, uniforms)
        receipt["evidence"]["shader_compile_link_and_draw_readback"] = True
        receipt["render"] = rendered
        receipt["render"]["resolution"] = [opts.size, opts.size]
        if receipt["shaders"]["default_checker"]:
            receipt["pixel_verification"] = check_default_pattern(data, opts.size, opts.size)
            if not receipt["pixel_verification"]["passed"]:
                raise RuntimeError("Known checker pixels did not match actual readback")
        receipt["png"] = write_png_rgba(output / "shader-readback.png", opts.size, opts.size, data)
        receipt["evidence"]["png_written"] = True
        receipt["shaders"]["input_sha256_after"] = {str(path): sha256(path.read_bytes()) for path in source_paths}
        receipt["shaders"]["inputs_unchanged"] = receipt["shaders"]["input_sha256_after"] == hashes_before
        if not receipt["shaders"]["inputs_unchanged"]:
            raise RuntimeError("Shader input files changed during rendering")
        receipt["ok"] = True
    except Exception as error:
        receipt["error"] = {"type": type(error).__name__, "message": str(error), "traceback": traceback.format_exc()}
    finally:
        if ctx is not None:
            ctx.release()
        receipt["elapsed_seconds"] = time.time() - started
        receipt_path = output / "gl-context-receipt.json"
        with receipt_path.open("x", encoding="utf-8") as stream:
            json.dump(receipt, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        print(json.dumps({"ok": receipt["ok"], "receipt": str(receipt_path), "context": {key: receipt.get("context", {}).get(key) for key in ("vendor", "renderer", "version", "shading_language_version")}, "png": receipt.get("png"), "error": receipt.get("error", {}).get("message")}, ensure_ascii=False))
    if not receipt["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
