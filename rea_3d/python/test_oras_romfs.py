"""Original synthetic NCSD/NCCH/IVFC/RomFS fixtures; no game assets."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "oras_romfs.py"
spec = importlib.util.spec_from_file_location("oras_romfs", SCRIPT)
romfs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(romfs)
S = 0xFFFFFFFF


def fixture():
    """Data has a non-0x1000 physical L3 distinct from the logical L3 offset."""
    directory = (struct.pack("<6I", 0, S, 24, 0, 24, 0)
                 + struct.pack("<6I", 0, S, S, 48, S, 2) + "a".encode("utf-16-le") + bytes(2))
    root_name, leaf_name = "root.txt".encode("utf-16-le"), "test.bin".encode("utf-16-le")
    files = (struct.pack("<IIQQII", 0, S, 16, 4, 48, len(root_name)) + root_name
             + struct.pack("<IIQQII", 24, S, 0, 13, S, len(leaf_name)) + leaf_name)
    table_offsets = [40, 44, 44 + len(directory), 48 + len(directory)]
    data_offset = ((table_offsets[3] + len(files) + 15) // 16) * 16
    l3 = bytearray(512)
    struct.pack_into("<10I", l3, 0, 40, 40, 4, table_offsets[1], len(directory),
                     table_offsets[2], 4, table_offsets[3], len(files), data_offset)
    struct.pack_into("<I", l3, table_offsets[0], 0)
    l3[table_offsets[1]:table_offsets[1]+len(directory)] = directory
    struct.pack_into("<I", l3, table_offsets[2], 0)
    l3[table_offsets[3]:table_offsets[3]+len(files)] = files
    l3[data_offset:data_offset+13] = b"CRAGsynthetic"
    l3[data_offset+16:data_offset+20] = b"note"
    ivfc = bytearray(1536)
    ivfc[:4] = b"IVFC"
    struct.pack_into("<II", ivfc, 4, 0x10000, 64)
    for index, (logical, size) in enumerate([(0, 128), (128, 256), (384, 512)]):
        struct.pack_into("<QQII", ivfc, 12 + index*24, logical, size, 6, 0)
    struct.pack_into("<II", ivfc, 84, 0x5C, 0)
    ivfc[192:704] = l3
    ncch = bytearray(2560)
    ncch[0x100:0x104] = b"NCCH"
    struct.pack_into("<I", ncch, 0x104, len(ncch)//512)
    ncch[0x150:0x160] = b"CTR-P-TEST".ljust(16, b"\0")
    ncch[0x18F] = 4
    struct.pack_into("<II", ncch, 0x1B0, 2, 3)
    ncch[1024:] = ivfc
    image = bytearray(512) + ncch
    image[0x100:0x104] = b"NCSD"
    struct.pack_into("<I", image, 0x104, len(image)//512)
    struct.pack_into("<II", image, 0x120, 1, len(ncch)//512)
    return image, {"partition": 512, "ivfc": 1536, "level3": 1728,
                   "dir": 1728 + table_offsets[1], "file": 1728 + table_offsets[3],
                   "payload": 1728 + data_offset}


class RomfsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rea3d-romfs-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "synthetic.3ds"
        self.data, self.at = fixture()
        self.source.write_bytes(self.data)

    def test_inventory_preserves_nested_absolute_offsets(self):
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        result = romfs.inspect_romfs(str(self.source))
        self.assertEqual(result["container"], "NCSD")
        self.assertEqual(result["product_code"], "CTR-P-TEST")
        self.assertEqual(result["level3_offset"], self.at["level3"])
        self.assertNotEqual(result["ivfc_levels"][2]["logical_offset"], 192)
        self.assertEqual(result["files"], [
            {"path": "a/test.bin", "absolute_offset": self.at["payload"], "size": 13},
            {"path": "root.txt", "absolute_offset": self.at["payload"]+16, "size": 4},
        ])
        self.assertFalse(result["file_payloads_read"])
        self.assertFalse(result["ivfc_hashes_verified"])
        self.assertFalse(result["decryption_performed"])
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), before)

    def test_standalone_ncch_offsets_are_relative_to_selected_input(self):
        source = self.root / "synthetic.ncch"
        source.write_bytes(self.data[512:])
        result = romfs.inspect_romfs(str(source))
        self.assertEqual(result["container"], "NCCH")
        self.assertEqual(result["files"][0]["absolute_offset"], self.at["payload"]-512)

    def test_single_selected_file_copy_and_no_overwrite(self):
        output = self.root / "one.garc"
        manifest = romfs.copy_romfs_file(str(self.source), "a/test.bin", str(output))
        self.assertEqual(output.read_bytes(), b"CRAGsynthetic")
        self.assertEqual(manifest["sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
        self.assertTrue(manifest["readback_verified"])
        with self.assertRaisesRegex(romfs.RomfsError, "already exists"):
            romfs.copy_romfs_file(str(self.source), "root.txt", str(output))
        self.assertEqual(output.read_bytes(), b"CRAGsynthetic")

    def test_unknown_traversal_wildcard_and_oversize_selection_do_not_create_output(self):
        output = self.root / "absent"
        for selected in ["unknown", "../a/test.bin", "/a/test.bin", "a//test.bin", "a/*", "a\\test.bin"]:
            with self.subTest(selected=selected), self.assertRaises(romfs.RomfsError):
                romfs.copy_romfs_file(str(self.source), selected, str(output))
            self.assertFalse(output.exists())
        with self.assertRaisesRegex(romfs.RomfsError, "byte limit"):
            romfs.copy_romfs_file(str(self.source), "a/test.bin", str(output), max_bytes=12)
        self.assertFalse(output.exists())

    def test_inventory_json_is_exclusive(self):
        output = self.root / "inventory.json"
        summary = romfs.write_inventory(str(self.source), str(output))
        self.assertEqual(summary["file_count"], 2)
        saved = output.read_bytes()
        self.assertEqual(json.loads(saved)["files"][0]["path"], "a/test.bin")
        with self.assertRaises(romfs.RomfsError):
            romfs.write_inventory(str(self.source), str(output))
        self.assertEqual(output.read_bytes(), saved)

    def test_malformed_headers_geometry_links_names_and_payloads_are_rejected(self):
        cases = [
            ("NCSD magic", 0x100, b"BAD!"),
            ("media exponent", 0x18E, bytes([31])),
            ("partition extent", 0x124, struct.pack("<I", 999)),
            ("NCCH magic", 512+0x100, b"BAD!"),
            ("content size", 512+0x104, struct.pack("<I", 999)),
            ("encryption", 512+0x18F, bytes([0])),
            ("no RomFS flag", 512+0x18F, bytes([6])),
            ("RomFS outside partition", 512+0x1B0, struct.pack("<I", 999)),
            ("IVFC magic", self.at["ivfc"], b"BAD!"),
            ("IVFC header size", self.at["ivfc"]+84, struct.pack("<I", 0)),
            ("IVFC master count", self.at["ivfc"]+8, struct.pack("<I", 32)),
            ("IVFC block exponent", self.at["ivfc"]+12+16, struct.pack("<I", 31)),
            ("IVFC logical overlap", self.at["ivfc"]+12+24, struct.pack("<Q", 0)),
            ("RomFS header", self.at["level3"], struct.pack("<I", 0)),
            ("table overlap", self.at["level3"]+12, struct.pack("<I", 40)),
            ("data offset", self.at["level3"]+36, struct.pack("<I", 40)),
            ("directory pointer interior", self.at["dir"]+8, struct.pack("<I", 25)),
            ("directory sibling cycle", self.at["dir"]+24+4, struct.pack("<I", 24)),
            ("directory parent mismatch", self.at["dir"]+24, struct.pack("<I", 24)),
            ("file sibling cycle", self.at["file"]+4, struct.pack("<I", 0)),
            ("file hash cycle", self.at["file"]+24, struct.pack("<I", 0)),
            ("file extent", self.at["file"]+16, struct.pack("<Q", 999)),
            ("file parent pointer", self.at["file"], struct.pack("<I", 25)),
            ("unsafe directory name", self.at["dir"]+48, "/".encode("utf-16-le")),
            ("UTF16 surrogate", self.at["dir"]+48, b"\x00\xd8"),
            ("unreachable file", self.at["dir"]+12, struct.pack("<I", S)),
        ]
        for label, offset, replacement in cases:
            with self.subTest(label=label):
                damaged = bytearray(self.data)
                damaged[offset:offset+len(replacement)] = replacement
                self.source.write_bytes(damaged)
                with self.assertRaises(romfs.RomfsError):
                    romfs.inspect_romfs(str(self.source))

    def test_truncated_partitions_and_headers_are_rejected(self):
        for length in [0, 128, 511, 512, 1023, 1600, len(self.data)-1]:
            with self.subTest(length=length):
                self.source.write_bytes(self.data[:length])
                with self.assertRaises(romfs.RomfsError):
                    romfs.inspect_romfs(str(self.source))

    def test_relative_paths_and_symlink_input_are_rejected(self):
        with self.assertRaises(romfs.RomfsError):
            romfs.inspect_romfs("relative.3ds")
        redirect = self.root / "redirect"
        try:
            redirect.symlink_to(self.source)
        except OSError as error:
            self.skipTest(f"host cannot create file symlink fixture: {error}")
        with self.assertRaisesRegex(romfs.RomfsError, "reparse"):
            romfs.inspect_romfs(str(redirect))

    def test_cli_lists_inventory_without_copying_any_payload(self):
        output = self.root / "cli.json"
        run = subprocess.run([sys.executable, "-I", str(SCRIPT), "list", "--input", str(self.source), "--output", str(output)],
                             capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        self.assertTrue(json.loads(run.stdout)["ok"])
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["file_count"], 2)


if __name__ == "__main__":
    unittest.main()
