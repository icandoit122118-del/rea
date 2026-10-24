"""Bounded read-only NCSD/NCCH -> CTR IVFC RomFS inventory and single-file copier.

No decryption, full filesystem extraction, model decoding, or game execution.
Format sources (pinned, examined independently of this implementation):
  https://github.com/3DSGuy/Project_CTR (ctrtool IvfcStream.cpp, romfs.h, ncch.h)
  https://github.com/Tyulis/3DSkit (unpack/RomFS.py, unpack/NCCH.py)
Source acquisition and SHA-256 records are stored on the caller's D: research path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import struct
import sys

SENTINEL = 0xFFFFFFFF
MAX_SOURCE = 8 * 1024**3
MAX_METADATA = 64 * 1024**2
MAX_FILES = 300_000
MAX_DIRECTORIES = 100_000
MAX_COPY_BYTES = 512 * 1024**2
MAX_NAME_BYTES = 2048
MAX_PATH_BYTES = 8192


class RomfsError(ValueError):
    pass


def _need(condition, message):
    if not condition:
        raise RomfsError(message)


def _range(offset, size, extent, label):
    _need(isinstance(offset, int) and isinstance(size, int) and offset >= 0 and size >= 0
          and offset <= extent and size <= extent - offset, f"out-of-bounds {label}")


def _align(value, alignment):
    return (value + alignment - 1) // alignment * alignment


def _u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def _absolute(value, label):
    _need(isinstance(value, (str, os.PathLike)), f"{label} must be a path")
    raw = os.fspath(value)
    _need(isinstance(raw, str) and raw and "\0" not in raw, f"invalid {label}")
    _need(Path(raw).is_absolute(), f"{label} must be absolute")
    _need(".." not in raw.replace("\\", "/").split("/"), f"traversal in {label}")
    if os.name == "nt":
        _need(not raw.startswith(("\\\\", "//")) and ":" not in raw[2:],
              f"UNC, device, or alternate stream path is outside this profile: {label}")
    return Path(os.path.abspath(raw))


def _reject_links(target):
    for part in [target, *target.parents]:
        entry = part.lstat()
        _need(not stat.S_ISLNK(entry.st_mode)
              and not (getattr(entry, "st_file_attributes", 0) & 0x400),
              "symlink/junction/reparse path rejected")


def _source_handle(value):
    source = _absolute(value, "source")
    _reject_links(source)
    handle = os.fdopen(os.open(source, os.O_RDONLY | getattr(os, "O_BINARY", 0)
                              | getattr(os, "O_NOFOLLOW", 0)), "rb")
    info = os.fstat(handle.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SOURCE:
        handle.close()
        raise RomfsError("source must be a regular file of at most 8 GiB")
    return source, handle, info


def _same_stat(a, b):
    return (a.st_dev, a.st_ino, a.st_size, a.st_mtime_ns, a.st_ctime_ns) == (
        b.st_dev, b.st_ino, b.st_size, b.st_mtime_ns, b.st_ctime_ns)


def _read(handle, offset, size, extent, label):
    _range(offset, size, extent, label)
    _need(size <= MAX_METADATA, f"bounded read exceeded for {label}")
    handle.seek(offset)
    data = handle.read(size)
    _need(len(data) == size, f"truncated {label}")
    return data


def _name(raw, *, root=False):
    _need(len(raw) <= MAX_NAME_BYTES and len(raw) % 2 == 0, "invalid UTF-16 name length")
    try:
        value = raw.decode("utf-16-le", errors="strict")
    except UnicodeDecodeError as exc:
        raise RomfsError("invalid UTF-16 name") from exc
    if root:
        _need(value == "", "root directory must have an empty name")
    else:
        _need(value not in {"", ".", ".."} and not any(c in value for c in "\0/\\"),
              "unsafe or empty RomFS path component")
    return value


def _records(blob, directory):
    """Parse metadata once; links must point to real record starts, not interiors."""
    records, cursor = {}, 0
    width = 24 if directory else 32
    maximum = MAX_DIRECTORIES if directory else MAX_FILES
    while cursor < len(blob):
        _range(cursor, width, len(blob), "directory/file metadata record")
        length = _u32(blob, cursor + width - 4)
        _need(length <= MAX_NAME_BYTES and length % 2 == 0, "invalid metadata name length")
        _range(cursor + width, length, len(blob), "metadata name")
        end = _align(cursor + width + length, 4)
        _range(cursor, end - cursor, len(blob), "aligned metadata record")
        parent, sibling = struct.unpack_from("<II", blob, cursor)
        raw_name = blob[cursor + width:cursor + width + length]
        entry = {"parent": parent, "sibling": sibling,
                 "name": _name(raw_name, root=directory and cursor == 0)}
        if directory:
            entry.update(zip(("child", "file", "hash_next"), struct.unpack_from("<III", blob, cursor + 8)))
        else:
            offset, size, hash_next = struct.unpack_from("<QQI", blob, cursor + 8)
            entry.update(data_relative_offset=offset, size=size, hash_next=hash_next)
        records[cursor] = entry
        _need(len(records) <= maximum, "metadata entry count exceeds limit")
        cursor = end
    return records


def _link(value, records, label):
    _need(value == SENTINEL or value in records, f"invalid {label} metadata pointer")


def _hash_links(buckets, records, label):
    _need(len(buckets) >= 4 and len(buckets) % 4 == 0, f"invalid {label} hash bucket geometry")
    for (value,) in struct.iter_unpack("<I", buckets):
        _link(value, records, label + " hash bucket")
    for entry in records.values():
        _link(entry["hash_next"], records, label + " hash sibling")
    done = set()
    for start in records:
        current, chain = start, set()
        while current != SENTINEL and current not in done:
            _need(current not in chain, f"cycle in {label} hash siblings")
            chain.add(current)
            current = records[current]["hash_next"]
        done.update(chain)


def inspect_romfs(source_path, partition_index=0):
    """Return bounded metadata and file extents, without reading file payloads.

    Paths are case-sensitive relative POSIX paths, e.g. ``a/0/0/8``. IVFC hash
    hierarchy sizes are checked structurally; SHA-256 tree/RSA authenticity is
    deliberately not claimed. Logical IVFC offsets are not physical offsets.
    """
    _need(type(partition_index) is int and 0 <= partition_index < 8, "partition index must be 0..7")
    source, handle, source_stat = _source_handle(source_path)
    with handle:
        size = source_stat.st_size
        top = _read(handle, 0, 512, size, "NCSD/NCCH header")
        if top[0x100:0x104] == b"NCSD":
            _need(top[0x18E] <= 8, "unsupported NCSD media unit exponent")
            ncsd_unit = 512 << top[0x18E]
            media_size = _u32(top, 0x104) * ncsd_unit
            _need(size <= media_size <= MAX_SOURCE, "invalid NCSD declared media size")
            selected_offset, selected_size = struct.unpack_from("<II", top, 0x120 + partition_index * 8)
            partition_offset, partition_size = selected_offset * ncsd_unit, selected_size * ncsd_unit
            _need(partition_offset >= 512 and partition_size >= 512, "selected NCSD partition is absent or overlaps header")
            _range(partition_offset, partition_size, size, "selected NCSD partition")
            container = "NCSD"
            ncch = _read(handle, partition_offset, 512, size, "NCCH header")
        elif top[0x100:0x104] == b"NCCH":
            _need(partition_index == 0, "standalone NCCH only accepts partition 0")
            partition_offset, partition_size, ncch, media_size, container = 0, size, top, size, "NCCH"
        else:
            raise RomfsError("input is not an NCSD or NCCH container")
        _need(ncch[0x100:0x104] == b"NCCH", "selected partition is not NCCH")
        flags = ncch[0x188:0x190]
        _need(flags[6] <= 8, "unsupported NCCH media unit exponent")
        unit = 512 << flags[6]
        content_size = _u32(ncch, 0x104) * unit
        _need(512 <= content_size <= partition_size, "NCCH content exceeds selected partition")
        _need(bool(flags[7] & 4), "encrypted NCCH is unsupported; no decryption is performed")
        _need(not (flags[7] & 2), "NCCH flags declare no mountable RomFS")
        romfs_relative = _u32(ncch, 0x1B0) * unit
        romfs_size = _u32(ncch, 0x1B4) * unit
        _need(romfs_relative >= 512 and romfs_size >= 0x60, "NCCH has no valid RomFS region")
        _range(romfs_relative, romfs_size, content_size, "NCCH RomFS region")
        romfs_offset = partition_offset + romfs_relative
        ivfc = _read(handle, romfs_offset, 0x60, size, "IVFC header")
        _need(ivfc[:4] == b"IVFC" and _u32(ivfc, 4) == 0x10000, "unsupported IVFC magic/type")
        _need(_u32(ivfc, 0x54) == 0x5C, "invalid CTR IVFC header size")
        master_size = _u32(ivfc, 8)
        _need(master_size >= 32 and master_size % 32 == 0, "invalid IVFC master hash size")
        levels = []
        for index in range(3):
            logical, level_size, exponent, _reserved = struct.unpack_from("<QQII", ivfc, 12 + index * 24)
            _need(5 <= exponent <= 24, "unsupported IVFC block size exponent")
            block = 1 << exponent
            _need(logical % block == 0 and level_size > 0, "invalid IVFC logical geometry")
            _range(logical, level_size, romfs_size, "IVFC logical level")
            levels.append({"logical_offset": logical, "size": level_size, "block_size": block})
        logical_ranges = sorted((l["logical_offset"], l["logical_offset"] + l["size"]) for l in levels)
        _need(all(logical_ranges[i][1] <= logical_ranges[i+1][0] for i in range(2)), "overlapping IVFC logical levels")
        # ctrtool IvfcStream::setBaseStream: physical order is master, data (L3), L1, L2.
        data_relative = _align(0x60 + master_size, levels[2]["block_size"])
        level1_relative = _align(data_relative + levels[2]["size"], levels[0]["block_size"])
        level2_relative = _align(level1_relative + levels[0]["size"], levels[1]["block_size"])
        for level, relative in zip(levels, (level1_relative, level2_relative, data_relative)):
            _range(relative, _align(level["size"], level["block_size"]), romfs_size, "physical IVFC level")
            level["absolute_offset"] = romfs_offset + relative
        _range(0x60, master_size, data_relative, "IVFC master hash table")
        _need(master_size == ((levels[0]["size"] + levels[0]["block_size"] - 1) // levels[0]["block_size"]) * 32,
              "IVFC master hash count does not match level 1")
        for parent, child in zip(levels, levels[1:]):
            _need(parent["size"] == ((child["size"] + child["block_size"] - 1) // child["block_size"]) * 32,
                  "IVFC hash layer count does not match next level")
        level3, level3_size = levels[2]["absolute_offset"], levels[2]["size"]
        header = _read(handle, level3, 40, size, "RomFS level 3 header")
        fields = struct.unpack("<10I", header)
        _need(fields[0] == 40, "invalid RomFS metadata header size")
        data_offset = fields[9]
        _need(40 <= data_offset <= level3_size and data_offset % 16 == 0, "invalid RomFS file data offset")
        table_specs = [(fields[i], fields[i+1]) for i in (1, 3, 5, 7)]
        _need(sum(length for _, length in table_specs) <= MAX_METADATA, "RomFS metadata exceeds 64 MiB")
        ordered = sorted(table_specs)
        previous_end = 40
        for offset, length in ordered:
            _need(offset % 4 == 0 and length % 4 == 0 and offset >= previous_end,
                  "overlapping or unaligned RomFS metadata tables")
            _range(offset, length, data_offset, "RomFS metadata table")
            previous_end = offset + length
        tables = [_read(handle, level3 + offset, length, size, "RomFS metadata table") for offset, length in table_specs]
        directories, files = _records(tables[1], True), _records(tables[3], False)
        _need(0 in directories, "RomFS root directory is missing")
        root = directories[0]
        _need(root["parent"] in (0, SENTINEL) and root["sibling"] == SENTINEL, "invalid RomFS root links")
        for entry in directories.values():
            _link(entry["parent"], directories, "directory parent")
            _link(entry["sibling"], directories, "directory sibling")
            _link(entry["child"], directories, "directory child")
            _link(entry["file"], files, "directory first-file")
        for entry in files.values():
            _need(entry["parent"] in directories, "file parent does not exist")
            _link(entry["sibling"], files, "file sibling")
            _range(entry["data_relative_offset"], entry["size"], level3_size - data_offset, "RomFS file extent")
        _hash_links(tables[0], directories, "directory")
        _hash_links(tables[2], files, "file")
        inventory, visited_dirs, visited_files, paths = [], set(), set(), set()
        pending = [(0, "", 0)]
        while pending:
            directory_index, prefix, depth = pending.pop()
            _need(depth <= 256, "RomFS directory depth exceeds 256")
            _need(directory_index not in visited_dirs, "cycle or reused RomFS directory")
            visited_dirs.add(directory_index)
            directory = directories[directory_index]
            child = directory["child"]
            while child != SENTINEL:
                _need(child not in visited_dirs, "cycle or reused child directory")
                entry = directories[child]
                _need(entry["parent"] == directory_index, "directory parent does not match tree")
                name = f"{prefix}/{entry['name']}" if prefix else entry["name"]
                _need(name not in paths and len(name.encode("utf-8")) <= MAX_PATH_BYTES, "duplicate or overlong RomFS path")
                paths.add(name)
                pending.append((child, name, depth + 1))
                child = entry["sibling"]
                _need(len(pending) <= MAX_DIRECTORIES, "directory sibling cycle or entry limit")
            child = directory["file"]
            while child != SENTINEL:
                _need(child not in visited_files, "cycle or reused RomFS file")
                visited_files.add(child)
                entry = files[child]
                _need(entry["parent"] == directory_index, "file parent does not match tree")
                name = f"{prefix}/{entry['name']}" if prefix else entry["name"]
                _need(name not in paths and len(name.encode("utf-8")) <= MAX_PATH_BYTES, "duplicate or overlong RomFS path")
                paths.add(name)
                inventory.append({"path": name, "absolute_offset": level3 + data_offset + entry["data_relative_offset"],
                                  "size": entry["size"]})
                child = entry["sibling"]
        _need(len(visited_dirs) == len(directories) and len(visited_files) == len(files), "unreachable RomFS metadata entries")
        _need(_same_stat(source_stat, os.fstat(handle.fileno())), "source changed during metadata read")
        inventory.sort(key=lambda item: item["path"])
        return {
            "schema": "rea3d.romfs.inventory.v1", "container": container,
            "source": {"path": str(source), "size": size, "mtime_ns": source_stat.st_mtime_ns,
                       "header_sha256": hashlib.sha256(top + ncch + ivfc).hexdigest(),
                       "full_file_sha256": None},
            "partition_index": partition_index, "partition_offset": partition_offset,
            "partition_size": partition_size, "ncch_content_size": content_size,
            "product_code": ncch[0x150:0x160].split(b"\0", 1)[0].decode("ascii", errors="strict"),
            "ncch_flags": flags.hex(), "encrypted": False,
            "romfs_offset": romfs_offset, "romfs_size": romfs_size,
            "ivfc_levels": levels, "level3_offset": level3, "level3_size": level3_size,
            "file_data_offset": level3 + data_offset,
            "metadata_sha256": hashlib.sha256(header + b"".join(tables)).hexdigest(),
            "directory_count": len(directories), "file_count": len(inventory), "files": inventory,
            "scope": "metadata_inventory_only", "file_payloads_read": False,
            "ivfc_hashes_verified": False, "rsa_signature_verified": False,
            "decryption_performed": False, "game_executed": False,
        }


def _new_output(value):
    output = _absolute(value, "output")
    _reject_links(output.parent)
    _need(not os.path.lexists(output), "output already exists; no overwrite permitted")
    return output


def write_inventory(source_path, output_path, partition_index=0):
    output = _new_output(output_path)
    inventory = inspect_romfs(source_path, partition_index)
    _reject_links(output.parent)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(inventory, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return {"output": str(output), "file_count": inventory["file_count"],
            "metadata_sha256": inventory["metadata_sha256"], "scope": inventory["scope"]}


def copy_romfs_file(source_path, romfs_path, output_path, partition_index=0, max_bytes=MAX_COPY_BYTES):
    """Stream exactly one caller-selected relative RomFS path to a NEW file."""
    _need(isinstance(romfs_path, str) and romfs_path and not romfs_path.startswith("/")
          and "\\" not in romfs_path and str(PurePosixPath(romfs_path)) == romfs_path
          and not any(p in {".", ".."} for p in romfs_path.split("/")), "path must exactly match an inventory relative path")
    _need(type(max_bytes) is int and 0 < max_bytes <= MAX_COPY_BYTES, "max_bytes must be 1..512 MiB")
    output = _new_output(output_path)
    inventory = inspect_romfs(source_path, partition_index)
    matches = [entry for entry in inventory["files"] if entry["path"] == romfs_path]
    _need(len(matches) == 1, "requested RomFS path was not found")
    entry = matches[0]
    _need(entry["size"] <= max_bytes, "requested file exceeds copy byte limit")
    source, source_handle, before = _source_handle(source_path)
    digest, total = hashlib.sha256(), 0
    with source_handle:
        _need(before.st_size == inventory["source"]["size"] and before.st_mtime_ns == inventory["source"]["mtime_ns"],
              "source changed after inventory")
        source_handle.seek(entry["absolute_offset"])
        _reject_links(output.parent)
        with output.open("xb") as destination:
            while total < entry["size"]:
                block = source_handle.read(min(1024**2, entry["size"] - total))
                _need(bool(block), "source truncated during selected-file copy")
                destination.write(block)
                digest.update(block)
                total += len(block)
            destination.flush()
        _need(_same_stat(before, os.fstat(source_handle.fileno())), "source changed during selected-file copy")
    _reject_links(output)
    readback = hashlib.sha256()
    with output.open("rb") as handle:
        while block := handle.read(1024**2):
            readback.update(block)
    _need(readback.digest() == digest.digest(), "copied file readback hash mismatch")
    return {"schema": "rea3d.romfs.copy.v1", "source": str(source), "romfs_path": romfs_path,
            "absolute_offset": entry["absolute_offset"], "size": total, "output": str(output),
            "sha256": digest.hexdigest(), "readback_verified": True,
            "scope": "single_romfs_file_copy_only", "decryption_performed": False,
            "ivfc_hashes_verified": False, "game_executed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("list", "extract"):
        item = sub.add_parser(command)
        item.add_argument("--input", required=True)
        item.add_argument("--output", required=True)
        item.add_argument("--partition", type=int, default=0)
        if command == "extract":
            item.add_argument("--path", required=True)
            item.add_argument("--max-bytes", type=int, default=MAX_COPY_BYTES)
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            result = write_inventory(args.input, args.output, args.partition)
        else:
            result = copy_romfs_file(args.input, args.path, args.output, args.partition, args.max_bytes)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
        return 0
    except (RomfsError, OSError, UnicodeError) as error:
        print(json.dumps({"ok": False, "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
