"""Synthetic typed metadata/bounds tests; never substitutes for native shader evidence."""
import importlib.util
import json
from pathlib import Path
import struct
import unittest
from test_pica_material import fixture
from rea3d_core.pica_material import PicaMaterialError, inspect_bch_materials, _Bch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit-shader-support.py"
spec = importlib.util.spec_from_file_location("shader_support_audit", SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def metadata_fixture(items):
    blob, positions = fixture()
    dictionary, entries, values = 0x800, 0x820, 0xA00
    string_cursor = 0x1100 + struct.unpack_from("<I", blob, 36)[0]
    def string(value, encoding="utf-8"):
        nonlocal string_cursor
        at = string_cursor
        raw = value.encode(encoding) + (b"\0\0" if encoding == "utf-16-le" else b"\0")
        blob[at:at + len(raw)] = raw
        string_cursor += len(raw)
        return at
    struct.pack_into("<I", blob, positions["params"] + 0x10C, dictionary)
    struct.pack_into("<III", blob, dictionary, entries if items else 0, len(items), 0)
    cursor = values
    for i, (name, kind, vals) in enumerate(items):
        name_at = string(name)
        value_at = cursor
        if kind == 0:
            raw = struct.pack("<" + "i" * len(vals), *vals)
        elif kind == 1:
            raw = struct.pack("<" + "f" * len(vals), *vals)
        elif kind in (2, 3):
            pointers = [string(x, "ascii" if kind == 2 else "utf-16-le") for x in vals]
            raw = struct.pack("<" + "I" * len(vals), *pointers)
        else:
            raw = bytes(12)
        blob[cursor:cursor + len(raw)] = raw
        cursor += len(raw)
        struct.pack_into("<IHHI", blob, entries + i * 12, name_at, kind, len(vals), value_at)
    struct.pack_into("<I", blob, 36, string_cursor - 0x1100)
    return blob, {**positions, "dictionary": dictionary, "entries": entries, "values": values}


def sidecar(blob):
    return audit.audit_bch(blob)["models"][0]["materials"][0]["user_metadata_sidecar"]


class MetadataAuditTests(unittest.TestCase):
    def test_absent_and_empty_metadata(self):
        self.assertEqual(sidecar(fixture()[0])["entries"], [])
        self.assertEqual(sidecar(metadata_fixture([])[0])["count"], 0)

    def test_integers_preserve_name_signed_values_and_raw_bits(self):
        blob, _ = metadata_fixture([("$EdgeType", 0, [1, -1, 2147483647])])
        report = sidecar(blob)
        entry = report["entries"][0]
        self.assertEqual(entry["name"], "$EdgeType")
        self.assertEqual(entry["values"], [1, -1, 2147483647])
        self.assertEqual(entry["raw_value_hex"], "01000000ffffffffffffff7f")
        self.assertFalse(report["runtime_effects_applied"])

    def test_float_values_retain_negative_zero_bits(self):
        blob, _ = metadata_fixture([("$RimPow", 1, [2.5, -0.0])])
        entry = sidecar(blob)["entries"][0]
        self.assertEqual(entry["values"], [2.5, 0.0])
        self.assertEqual(entry["raw_value_hex"], "0000204000000080")

    def test_nonfinite_float_is_json_safe_with_original_bits(self):
        blob, p = metadata_fixture([("$RawFloat", 1, [0.0, 0.0])])
        struct.pack_into("<II", blob, p["values"], 0x7FC01234, 0xFF800000)
        report = sidecar(blob)
        self.assertEqual(report["entries"][0]["values"], [None, None])
        self.assertEqual(report["entries"][0]["raw_value_hex"], "3412c07f000080ff")
        json.dumps(report, allow_nan=False)

    def test_ascii_unicode_metadata_values_and_terminators(self):
        blob, _ = metadata_fixture([("$Shader", 2, ["1@PokePack"]), ("$Label", 3, ["이상해씨"])])
        entries = sidecar(blob)["entries"]
        self.assertEqual(entries[0]["values"], ["1@PokePack"])
        self.assertEqual(entries[1]["values"], ["이상해씨"])
        self.assertTrue(entries[1]["strings"][0]["raw_source_hex"].endswith("0000"))

    def test_relocation_preserves_original_pointer_and_decodes_value(self):
        blob, p = metadata_fixture([("$EdgeID", 0, [7])])
        contents, strings = 68, 0x1100
        relocations = []
        for at, target, base in [(p["params"] + 0x10C, 0, contents),
                                 (p["dictionary"], 0, contents),
                                 (p["entries"], 1, strings),
                                 (p["entries"] + 8, 0, contents)]:
            current = struct.unpack_from("<I", blob, at)[0]
            struct.pack_into("<I", blob, at, current - base)
            pointer_index = at - contents if target == 1 else (at - contents) >> 2
            relocations.append(pointer_index | (target << 25))
        start = len(blob)
        blob.extend(struct.pack("<4I", *relocations))
        struct.pack_into("<I", blob, 28, start)
        struct.pack_into("<I", blob, 52, 16)
        report = sidecar(blob)
        self.assertEqual(report["dictionary_offset"], p["dictionary"])
        self.assertEqual(report["pointer_raw_source_hex"], struct.pack("<I", p["dictionary"] - contents).hex())
        self.assertEqual(report["entries"][0]["values"], [7])

    def test_unknown_and_complex_types_remain_explicitly_partial(self):
        for kind in (5, 65535):
            with self.subTest(kind=kind):
                blob, _ = metadata_fixture([("$Other", kind, [0])])
                report = sidecar(blob)
                self.assertFalse(report["complete"])
                self.assertFalse(report["entries"][0]["decoded"])
                self.assertNotIn("values", report["entries"][0])

    def test_bounding_box_retains_15_floats_without_render_semantics(self):
        blob, p = metadata_fixture([("OBBox", 4, [0])])
        struct.pack_into("<15f", blob, p["values"], *range(15))
        entry = sidecar(blob)["entries"][0]
        self.assertEqual(entry["values"], list(map(float, range(15))))
        self.assertEqual(len(bytes.fromhex(entry["raw_value_hex"])), 60)

    def test_cross_material_aliases_cannot_bypass_total_value_budget(self):
        blob, p = metadata_fixture([("$A", 0, [1])])
        bch = _Bch(blob)
        bch.metadata_total_values = audit.MAX_TOTAL_VALUES
        with self.assertRaises(PicaMaterialError):
            audit.read_metadata(bch, p["params"] + 0x10C)

    def test_out_of_bounds_dictionary_and_values_are_rejected(self):
        for field in ("dictionary", "values"):
            with self.subTest(field=field):
                blob, p = metadata_fixture([("$A", 0, [1])])
                at = p["params"] + 0x10C if field == "dictionary" else p["entries"] + 8
                struct.pack_into("<I", blob, at, len(blob) - 1)
                with self.assertRaises(PicaMaterialError):
                    sidecar(blob)

    def test_count_budget_and_truncated_value_array_are_rejected(self):
        for mutation in ("entries", "values"):
            with self.subTest(mutation=mutation):
                blob, p = metadata_fixture([("$A", 0, [1])])
                if mutation == "entries":
                    struct.pack_into("<I", blob, p["dictionary"] + 4, 1025)
                else:
                    struct.pack_into("<H", blob, p["entries"] + 6, 16385)
                with self.assertRaises(PicaMaterialError):
                    sidecar(blob)

    def test_unterminated_string_is_rejected(self):
        blob, p = metadata_fixture([("$A", 2, ["x"])])
        string_at = struct.unpack_from("<I", blob, p["values"])[0]
        struct.pack_into("<I", blob, 36, string_at + 1 - 0x1100)
        with self.assertRaises(PicaMaterialError):
            sidecar(blob)

    def test_read_only_sidecar_does_not_change_exporter_baseline(self):
        blob, _ = metadata_fixture([("$EdgeType", 0, [1]), ("$PhongPow", 1, [10.0])])
        before = bytes(blob)
        baseline = inspect_bch_materials(blob)
        extended = audit.audit_bch(blob)
        material = extended["models"][0]["materials"][0]
        self.assertNotIn("user_metadata_sidecar", baseline["models"][0]["materials"][0])
        material.pop("user_metadata_sidecar")
        extended.pop("metadata_scope")
        self.assertEqual(extended, baseline)
        self.assertEqual(bytes(blob), before)

    def test_container_header_overlap_and_expansion_budget_rejected(self):
        bad = b"PC" + struct.pack("<HII", 1, 0, 12)
        with self.assertRaises(PicaMaterialError):
            list(audit.bch_leaves(bad, "bad", [0, 0]))
        with self.assertRaises(PicaMaterialError):
            list(audit.bch_leaves(b"\x11\x04\x00\x00", "lz", [audit.MAX_EXPANDED, 0]))


if __name__ == "__main__":
    unittest.main()
