"""Package real GPU-rendered PNG frames as APNG; no pixel synthesis or edits."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import zlib


def chunks(blob):
    if blob[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Not PNG")
    offset = 8
    while offset < len(blob):
        size, = struct.unpack_from(">I", blob, offset)
        tag, data = blob[offset+4:offset+8], blob[offset+8:offset+8+size]
        if offset+12+size > len(blob) or zlib.crc32(tag+data) & 0xffffffff != struct.unpack_from(">I", blob, offset+8+size)[0]:
            raise ValueError("Bad PNG chunk or CRC")
        yield tag, data
        offset += size+12


def chunk(tag, data):
    return struct.pack(">I", len(data))+tag+data+struct.pack(">I", zlib.crc32(tag+data) & 0xffffffff)


parser = argparse.ArgumentParser()
parser.add_argument("--receipt", required=True)
parser.add_argument("--start", type=int, required=True)
parser.add_argument("--count", type=int, required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--fps", type=int, default=30)
args = parser.parse_args()
receipt = json.loads(Path(args.receipt).read_text(encoding="utf-8"))
frames = receipt["frames"][args.start:args.start+args.count]
if len(frames) != args.count or not 1 <= args.count <= 128 or not 1 <= args.fps <= 120:
    raise ValueError("Frame range/fps invalid")
output = Path(args.output)
if not output.is_absolute() or output.exists():
    raise ValueError("Select a new absolute output file")
payload = bytearray(b"\x89PNG\r\n\x1a\n")
sequence, header, sources = 0, None, []
for index, frame in enumerate(frames):
    name = frame.get("png_path") or frame.get("path") or frame.get("png", {}).get("path")
    if not name:
        raise ValueError("Frame has no PNG path")
    source = Path(name)
    blob = source.read_bytes()
    parts = list(chunks(blob))
    ihdr = next(data for tag,data in parts if tag == b"IHDR")
    if header is None:
        header = ihdr
        width,height = struct.unpack_from(">II", header)
        payload.extend(chunk(b"IHDR", header))
        payload.extend(chunk(b"acTL", struct.pack(">II", len(frames), 0)))
    elif ihdr != header:
        raise ValueError("Frame dimensions/format differ")
    control = struct.pack(">IIIIIHHBB", sequence, width,height,0,0,1,args.fps,0,0)
    sequence += 1
    payload.extend(chunk(b"fcTL", control))
    for tag,data in parts:
        if tag != b"IDAT":
            continue
        if index == 0:
            payload.extend(chunk(b"IDAT", data))
        else:
            payload.extend(chunk(b"fdAT", struct.pack(">I",sequence)+data))
            sequence += 1
    sources.append({"path":str(source), "sha256":hashlib.sha256(blob).hexdigest()})
payload.extend(chunk(b"IEND", b""))
with output.open("xb") as handle:
    handle.write(payload)
manifest = {"execution":"lossless_packaging_of_actual_gpu_frames", "apng":str(output),
            "sha256":hashlib.sha256(payload).hexdigest(),"frames":len(frames), "fps":args.fps,
            "repeat":"presentation loop; original-game loop control not inferred", "source_pngs":sources}
with output.with_suffix(".json").open("x", encoding="utf-8") as handle:
    json.dump(manifest,handle,indent=2)
print(json.dumps({key:value for key,value in manifest.items() if key != "source_pngs"}))
