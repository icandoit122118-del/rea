"""Read bounded GARC tables inside a selected RomFS file and copy explicit members.

Does not copy the complete archive, relax native GARC size limits, decompress,
decode assets, decrypt, or execute a game. NCSD/NCCH/IVFC bounds use oras_romfs.
GARC layout follows the pinned upstream sources in research/garc-sources.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys

_romfs_spec = importlib.util.spec_from_file_location("oras_romfs", Path(__file__).with_name("oras_romfs.py"))
romfs = importlib.util.module_from_spec(_romfs_spec)
_romfs_spec.loader.exec_module(romfs)

MAX_ENTRIES = 10_000
MAX_TABLE_BYTES = 4 * 1024**2
MAX_SELECTION = 128
MAX_MEMBER_BYTES = 64 * 1024**2
MAX_SELECTED_TOTAL = 128 * 1024**2


def _load_inventory(source_path, inventory_path, archive_path):
    inventory_file = romfs._absolute(inventory_path, "inventory")
    romfs._reject_links(inventory_file)
    romfs._need(inventory_file.stat().st_size <= 64 * 1024**2, "inventory exceeds byte limit")
    supplied = json.loads(inventory_file.read_text(encoding="utf-8"))
    romfs._need(isinstance(supplied, dict) and isinstance(supplied.get("files"), list)
                and all(isinstance(entry, dict) for entry in supplied["files"]), "invalid inventory JSON shape")
    fresh = romfs.inspect_romfs(source_path, supplied.get("partition_index", 0))
    romfs._need(supplied.get("source") == fresh["source"]
                and supplied.get("metadata_sha256") == fresh["metadata_sha256"], "inventory does not match current source")
    expected = [f for f in fresh["files"] if f["path"] == archive_path]
    reported = [f for f in supplied.get("files", []) if f.get("path") == archive_path]
    romfs._need(len(expected) == 1 and reported == expected, "selected archive extent differs from verified RomFS metadata")
    return fresh, expected[0]


def _parse(handle, source_size, archive, *, prefix_count=20):
    base, extent = archive["absolute_offset"], archive["size"]
    romfs._range(base, extent, source_size, "selected archive in source")
    romfs._need(28 <= extent <= 0xFFFFFFFF, "archive is outside GARC 32-bit size profile")

    def read(offset, size, label):
        romfs._range(offset, size, extent, label)
        romfs._need(size <= MAX_TABLE_BYTES, "GARC table read exceeds 4 MiB")
        return romfs._read(handle, base + offset, size, source_size, label)

    initial = read(0, 28, "GARC header")
    romfs._need(initial[:4] == b"CRAG", "selected RomFS file is not GARC/CRAG")
    marker = struct.unpack_from("<H", initial, 8)[0]
    romfs._need(marker in {0xFEFF, 0xFFFE}, "invalid GARC BOM")
    endian = "<" if marker == 0xFEFF else ">"
    u16 = lambda data, offset: struct.unpack_from(endian + "H", data, offset)[0]
    u32 = lambda data, offset: struct.unpack_from(endian + "I", data, offset)[0]
    raw_version, header_size = u16(initial, 10), u32(initial, 4)
    romfs._need(raw_version in {0x0400, 0x0600}, "unsupported GARC version")
    version = raw_version >> 8
    romfs._need(header_size == (28 if version == 4 else 36), "GARC header size mismatch")
    header = read(0, header_size, "versioned GARC header")
    romfs._need(u32(header, 12) == 4 and u32(header, 20) == extent, "GARC chunk count or file size mismatch")
    data_offset = u32(header, 16)
    largest = u32(header, 24 if version == 4 else 28)
    largest_padded = None if version == 4 else u32(header, 24)
    alignment = 4 if version == 4 else u32(header, 32)
    romfs._need(0 < alignment <= 1024**2 and alignment & (alignment-1) == 0, "invalid GARC alignment")
    fato_header = read(header_size, 12, "FATO header")
    romfs._need(fato_header[:4] == b"OTAF", "invalid FATO magic")
    fato_size, group_count = u32(fato_header, 4), u16(fato_header, 8)
    romfs._need(group_count <= MAX_ENTRIES and fato_size == 12 + group_count*4
                and u16(fato_header, 10) == 0xFFFF, "invalid FATO count, size, or reserved padding")
    fato = read(header_size, fato_size, "FATO table")
    fatb_offset = header_size + fato_size
    fatb_header = read(fatb_offset, 12, "FATB header")
    romfs._need(fatb_header[:4] == b"BTAF", "invalid FATB magic")
    fatb_size, file_count = u32(fatb_header, 4), u32(fatb_header, 8)
    romfs._need(12 <= fatb_size <= MAX_TABLE_BYTES and file_count <= MAX_ENTRIES, "invalid FATB size or count")
    fatb = read(fatb_offset, fatb_size, "FATB table")
    fimb_offset = fatb_offset + fatb_size
    fimb = read(fimb_offset, 12, "FIMB header")
    romfs._need(fimb[:4] == b"BMIF" and u32(fimb, 4) == 12, "invalid FIMB header")
    data_size = u32(fimb, 8)
    romfs._need(data_offset == fimb_offset + 12 and data_size == extent-data_offset, "GARC data offset/size mismatch")
    entries, record_ranges, payload_ranges = [], [], []
    maximum, maximum_padded = 0, 0
    for index in range(group_count):
        pointer = u32(fato, 12 + index*4)
        romfs._need(pointer % 4 == 0, "unaligned FATO pointer")
        cursor = 12 + pointer
        romfs._range(cursor, 4, fatb_size, "FATB bitmask")
        start_record, mask = cursor, u32(fatb, cursor)
        cursor += 4
        for subindex in range(32):
            if not (mask >> subindex) & 1:
                continue
            romfs._range(cursor, 12, fatb_size, "FATB subentry")
            start, end, length = struct.unpack_from(endian + "III", fatb, cursor)
            cursor += 12
            romfs._need(start <= end and length <= end-start, "invalid GARC member start/end/size")
            romfs._range(start, end-start, data_size, "GARC member data range")
            romfs._need(len(entries) < MAX_ENTRIES, "GARC entry limit exceeded")
            entries.append({"index": index, "subindex": subindex, "size": length,
                            "padded_size": end-start, "archive_relative_offset": data_offset+start,
                            "rom_absolute_offset": base+data_offset+start})
            if end > start:
                payload_ranges.append((start, end))
            maximum, maximum_padded = max(maximum, length), max(maximum_padded, end-start)
        record_ranges.append((start_record, cursor))
    romfs._need(len(entries) == file_count, "GARC leaf count mismatch")
    cursor = 12
    for start, end in sorted(record_ranges):
        romfs._need(start == cursor, "overlapping or unreferenced FATB record")
        cursor = end
    romfs._need(cursor == fatb_size, "unreferenced FATB bytes")
    payload_ranges.sort()
    romfs._need(all(payload_ranges[i][1] <= payload_ranges[i+1][0] for i in range(len(payload_ranges)-1)),
                "overlapping GARC member extents are unsupported")
    romfs._need(maximum <= largest <= data_size, "invalid GARC largest member declaration")
    if largest_padded is not None:
        romfs._need(maximum_padded <= largest_padded <= data_size and largest_padded >= largest,
                    "invalid GARC largest padded declaration")
    for entry in entries[:prefix_count]:
        prefix = read(entry["archive_relative_offset"], min(16, entry["size"]), "selected member prefix")
        entry.update(magic_hex=prefix.hex(), magic_ascii=prefix[:4].decode("ascii", errors="replace"))
    return {"format": "GARC", "version": version, "byte_order": "little" if endian == "<" else "big",
            "archive": archive, "group_count": group_count, "file_count": file_count,
            "data_offset": data_offset, "data_size": data_size, "fato_size": fato_size, "fatb_size": fatb_size,
            "table_sha256": hashlib.sha256(header+fato+fatb+fimb).hexdigest(), "entries": entries,
            "prefixes_read": min(prefix_count, len(entries)), "entire_archive_read": False,
            "scope": "bounded_archive_tables_and_selected_prefixes", "decompressed": False,
            "decoded_models": False, "decoded_animations": False}


def inspect_archive(source_path, inventory_path, archive_path, prefix_count=20):
    romfs._need(type(prefix_count) is int and 0 <= prefix_count <= 128, "prefix count must be 0..128")
    inventory, archive = _load_inventory(source_path, inventory_path, archive_path)
    source, handle, before = romfs._source_handle(source_path)
    with handle:
        romfs._need(before.st_size == inventory["source"]["size"] and before.st_mtime_ns == inventory["source"]["mtime_ns"], "source changed after inventory")
        result = _parse(handle, before.st_size, archive, prefix_count=prefix_count)
        romfs._need(romfs._same_stat(before, os.fstat(handle.fileno())), "source changed during GARC inventory")
    return {"schema": "rea3d.romfs.garc.inventory.v1", "source": inventory["source"],
            "romfs_metadata_sha256": inventory["metadata_sha256"], **result}


def extract_members(source_path, inventory_path, archive_path, indices, output_dir):
    romfs._need(isinstance(indices, list) and 0 < len(indices) <= MAX_SELECTION, "explicit selection must contain 1..128 index pairs")
    wanted = []
    for pair in indices:
        romfs._need(isinstance(pair, (tuple, list)) and len(pair) == 2
                    and all(type(value) is int for value in pair) and 0 <= pair[0] < MAX_ENTRIES
                    and 0 <= pair[1] < 32, "selection must contain [index, subindex] pairs")
        wanted.append(tuple(pair))
    romfs._need(len(set(wanted)) == len(wanted), "duplicate member selection")
    output = romfs._new_output(output_dir)
    report = inspect_archive(source_path, inventory_path, archive_path)
    by_pair = {(entry["index"], entry["subindex"]): entry for entry in report["entries"]}
    romfs._need(all(pair in by_pair for pair in wanted), "selected member does not exist")
    selected = [dict(by_pair[pair]) for pair in wanted]
    romfs._need(all(entry["size"] <= MAX_MEMBER_BYTES for entry in selected)
                and sum(entry["size"] for entry in selected) <= MAX_SELECTED_TOTAL,
                "selected member bytes exceed bounded sample limits")
    source, handle, before = romfs._source_handle(source_path)
    with handle:
        romfs._need(before.st_size == report["source"]["size"] and before.st_mtime_ns == report["source"]["mtime_ns"], "source changed after table inventory")
        romfs._reject_links(output.parent)
        output.mkdir(exist_ok=False)
        for entry in selected:
            romfs._reject_links(output)
            filename = f"{entry['index']:05d}_{entry['subindex']:02d}.bin"
            target = output / filename
            digest, prefix, count = hashlib.sha256(), bytearray(), 0
            handle.seek(entry["rom_absolute_offset"])
            with target.open("xb") as destination:
                while count < entry["size"]:
                    block = handle.read(min(1024**2, entry["size"]-count))
                    romfs._need(bool(block), "source truncated during selected member copy")
                    destination.write(block)
                    digest.update(block)
                    prefix.extend(block[:max(0, 16-len(prefix))])
                    count += len(block)
            readback = hashlib.sha256()
            romfs._reject_links(target)
            with target.open("rb") as saved:
                remaining = entry["size"]
                while remaining:
                    block = saved.read(min(1024**2, remaining))
                    romfs._need(bool(block), "truncated member readback")
                    readback.update(block)
                    remaining -= len(block)
                romfs._need(not saved.read(1), "member grew during readback")
            romfs._need(readback.digest() == digest.digest(), "member readback hash mismatch")
            entry.update(output_path=str(target), relative_path=filename, sha256=digest.hexdigest(),
                         magic_hex=prefix.hex(), magic_ascii=prefix[:4].decode("ascii", errors="replace"), readback_verified=True)
        romfs._need(romfs._same_stat(before, os.fstat(handle.fileno())), "source changed during member copy")
    manifest = {"schema": "rea3d.romfs.garc.selection.v1", "source": report["source"],
                "archive": report["archive"], "table_sha256": report["table_sha256"],
                "romfs_metadata_sha256": report["romfs_metadata_sha256"], "entries": selected,
                "copied_member_count": len(selected), "copied_bytes": sum(entry["size"] for entry in selected),
                "entire_archive_read": False, "decompressed": False, "decoded_models": False,
                "decoded_animations": False, "game_executed": False, "scope": "selected_raw_archive_members_only"}
    romfs._reject_links(output)
    with (output / "manifest.json").open("x", encoding="utf-8") as saved:
        json.dump(manifest, saved, ensure_ascii=False, indent=2)
        saved.write("\n")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("inspect", "extract"):
        item = sub.add_parser(command)
        item.add_argument("--input", required=True)
        item.add_argument("--inventory", required=True)
        item.add_argument("--path", required=True)
        item.add_argument("--output", required=True)
        if command == "extract":
            item.add_argument("--indices", required=True, help="Explicit comma-separated index:subindex pairs; bare index means subindex 0")
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            output = romfs._new_output(args.output)
            report = inspect_archive(args.input, args.inventory, args.path)
            romfs._reject_links(output.parent)
            with output.open("x", encoding="utf-8") as saved:
                json.dump(report, saved, ensure_ascii=False, indent=2)
                saved.write("\n")
            summary = {"output": str(output), "file_count": report["file_count"], "table_sha256": report["table_sha256"]}
        else:
            selection = [tuple(map(int, value.split(":"))) if ":" in value else (int(value), 0)
                         for value in args.indices.split(",")]
            report = extract_members(args.input, args.inventory, args.path, selection, args.output)
            summary = {"manifest": str(Path(args.output) / "manifest.json"), "copied_member_count": report["copied_member_count"], "copied_bytes": report["copied_bytes"]}
        print(json.dumps({"ok": True, "result": summary}))
        return 0
    except (romfs.RomfsError, OSError, ValueError, TypeError, KeyError) as error:
        print(json.dumps({"ok": False, "error": f"{type(error).__name__}: {error}"}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
