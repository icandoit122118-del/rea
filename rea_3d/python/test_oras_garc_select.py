"""Small synthetic nested GARC selection tests; no commercial game bytes."""
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_oras_romfs import fixture as romfs_fixture

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "oras_garc_select.py"
spec = importlib.util.spec_from_file_location("oras_garc_select", SCRIPT)
selector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(selector)


def garc_fixture(version=4, big=False):
    endian = ">" if big else "<"
    header_size = 28 if version == 4 else 36
    fato_size, fatb_size = 20, 56
    data_offset = header_size + fato_size + fatb_size + 12
    result = bytearray(data_offset + 16)
    u32 = lambda at, value: struct.pack_into(endian+"I", result, at, value)
    u16 = lambda at, value: struct.pack_into(endian+"H", result, at, value)
    result[:4] = b"CRAG"
    u32(4, header_size); u16(8, 0xFEFF); u16(10, version*0x100)
    u32(12, 4); u32(16, data_offset); u32(20, len(result)); u32(24, 5 if version == 4 else 8)
    if version == 6:
        u32(28, 5); u32(32, 4)
    fato = header_size
    result[fato:fato+4] = b"OTAF"; u32(fato+4, fato_size); u16(fato+8, 2); u16(fato+10, 0xFFFF)
    u32(fato+12, 0); u32(fato+16, 16)
    fatb = fato + fato_size
    result[fatb:fatb+4] = b"BTAF"; u32(fatb+4, fatb_size); u32(fatb+8, 3)
    u32(fatb+12, 1)
    for at, values in [(fatb+16, (0, 4, 3)), (fatb+32, (4, 12, 5)), (fatb+44, (12, 16, 1))]:
        struct.pack_into(endian+"III", result, at, *values)
    u32(fatb+28, (1<<2) | (1<<31))
    fimb = fatb+fatb_size
    result[fimb:fimb+4] = b"BMIF"; u32(fimb+4, 12); u32(fimb+8, 16)
    result[data_offset:data_offset+3] = b"abc"
    result[data_offset+4:data_offset+9] = b"12345"
    result[data_offset+12:data_offset+13] = b"Z"
    return result, {"header_size": header_size, "fato": fato, "fatb": fatb, "data": data_offset, "fimb": fimb}


class GarcSelectorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rea3d-garc-select-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source, self.inventory = self.root/"source.3ds", self.root/"inventory.json"
        self.prepare()

    def prepare(self, version=4, big=False, mutate=None):
        self.image, self.offsets = romfs_fixture()
        self.archive, self.garc_offsets = garc_fixture(version, big)
        if mutate:
            mutate(self.archive, self.garc_offsets)
        start = self.offsets["payload"]
        self.image[start:start+len(self.archive)] = self.archive
        struct.pack_into("<Q", self.image, self.offsets["file"]+48+16, len(self.archive))
        struct.pack_into("<Q", self.image, self.offsets["file"]+8, 256)
        self.image[start+256:start+260] = b"note"
        self.source.write_bytes(self.image)
        self.inventory.write_text(json.dumps(selector.romfs.inspect_romfs(str(self.source))), encoding="utf-8")

    def inspect(self):
        return selector.inspect_archive(str(self.source), str(self.inventory), "a/test.bin")

    def test_v4_v6_and_both_endians_bound_member_offsets(self):
        for version in (4, 6):
            for big in (False, True):
                with self.subTest(version=version, big=big):
                    self.prepare(version, big)
                    report = self.inspect()
                    self.assertEqual(report["version"], version)
                    self.assertEqual(report["file_count"], 3)
                    self.assertEqual(report["byte_order"], "big" if big else "little")
                    self.assertEqual([(x["index"], x["subindex"], x["size"]) for x in report["entries"]], [(0, 0, 3), (1, 2, 5), (1, 31, 1)])
                    self.assertEqual(report["entries"][0]["rom_absolute_offset"], self.offsets["payload"]+self.garc_offsets["data"])
                    self.assertEqual(report["entries"][1]["magic_hex"], b"12345".hex())
                    self.assertFalse(report["entire_archive_read"])

    def test_explicit_selection_hashes_and_provenance(self):
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        output = self.root/"selected"
        result = selector.extract_members(str(self.source), str(self.inventory), "a/test.bin", [(1, 31), (0, 0)], str(output))
        self.assertEqual((result["copied_member_count"], result["copied_bytes"]), (2, 4))
        self.assertEqual(sorted(p.name for p in output.iterdir()), ["00000_00.bin", "00001_31.bin", "manifest.json"])
        self.assertEqual((output/"00001_31.bin").read_bytes(), b"Z")
        self.assertEqual(result["entries"][0]["sha256"], hashlib.sha256(b"Z").hexdigest())
        self.assertEqual(json.loads((output/"manifest.json").read_text(encoding="utf-8")), result)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), before)

    def test_existing_directory_and_invalid_selection_are_refused(self):
        existing = self.root/"existing"; existing.mkdir(); (existing/"keep").write_text("safe")
        with self.assertRaises(selector.romfs.RomfsError):
            selector.extract_members(str(self.source), str(self.inventory), "a/test.bin", [(0, 0)], str(existing))
        self.assertEqual((existing/"keep").read_text(), "safe")
        for index, choice in enumerate([[], [(0, 0), (0, 0)], [(99, 0)], [(True, 0)], [(0, 33)]]):
            output = self.root/f"bad-{index}"
            with self.subTest(choice=choice), self.assertRaises(selector.romfs.RomfsError):
                selector.extract_members(str(self.source), str(self.inventory), "a/test.bin", choice, str(output))
            self.assertFalse(output.exists())
        with patch.object(selector, "MAX_MEMBER_BYTES", 4), self.assertRaises(selector.romfs.RomfsError):
            selector.extract_members(str(self.source), str(self.inventory), "a/test.bin", [(1, 2)], str(self.root/"oversize"))
        self.assertFalse((self.root/"oversize").exists())

    def test_inventory_cross_range_and_source_changes_are_rejected(self):
        supplied = json.loads(self.inventory.read_text())
        supplied["files"][0]["absolute_offset"] += 1
        self.inventory.write_text(json.dumps(supplied))
        with self.assertRaisesRegex(selector.romfs.RomfsError, "extent"):
            self.inspect()
        self.prepare()
        self.source.write_bytes(self.source.read_bytes()+b"changed")
        with self.assertRaises(selector.romfs.RomfsError):
            self.inspect()

    def test_corrupt_tables_payload_cross_ranges_and_overlap_are_rejected(self):
        cases = [
            ("magic", lambda b, p: b.__setitem__(slice(0, 4), b"BAD!")),
            ("BOM", lambda b, p: struct.pack_into("<H", b, 8, 0)),
            ("size", lambda b, p: struct.pack_into("<I", b, 20, len(b)+1)),
            ("data_offset", lambda b, p: struct.pack_into("<I", b, 16, p["data"]+1)),
            ("FATO pointer", lambda b, p: struct.pack_into("<I", b, p["fato"]+12, 0xFFFFFFFC)),
            ("duplicate FATO pointer", lambda b, p: struct.pack_into("<I", b, p["fato"]+16, 0)),
            ("FATB count", lambda b, p: struct.pack_into("<I", b, p["fatb"]+8, 4)),
            ("member crosses archive", lambda b, p: struct.pack_into("<I", b, p["fatb"]+20, 99999)),
            ("member length", lambda b, p: struct.pack_into("<I", b, p["fatb"]+24, 99999)),
            ("payload overlap", lambda b, p: struct.pack_into("<I", b, p["fatb"]+32, 0)),
            ("FIMB size", lambda b, p: struct.pack_into("<I", b, p["fimb"]+8, 99999)),
        ]
        for label, mutate in cases:
            with self.subTest(label=label):
                self.prepare(mutate=mutate)
                with self.assertRaises(selector.romfs.RomfsError):
                    self.inspect()

    def test_actual_cli_inspect_and_extract_under_isolated_python(self):
        output = self.root/"cli.json"
        common = [sys.executable, "-I", str(SCRIPT)]
        inputs = ["--input", str(self.source), "--inventory", str(self.inventory), "--path", "a/test.bin"]
        inspected = subprocess.run(common+["inspect", *inputs, "--output", str(output)], capture_output=True, text=True, timeout=15)
        self.assertEqual(inspected.returncode, 0, inspected.stdout+inspected.stderr)
        self.assertEqual(json.loads(output.read_text())["file_count"], 3)
        extracted = subprocess.run(common+["extract", *inputs, "--output", str(self.root/"cli-output"), "--indices", "0,1:31"], capture_output=True, text=True, timeout=15)
        self.assertEqual(extracted.returncode, 0, extracted.stdout+extracted.stderr)
        self.assertEqual(json.loads(extracted.stdout)["result"]["copied_member_count"], 2)


if __name__ == "__main__":
    unittest.main()
