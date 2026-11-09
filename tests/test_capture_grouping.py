import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

NODE = shutil.which("node")
ROOT = Path(__file__).resolve().parents[1]
START = "// ---- capture grouping ----"
END = "// ---- end capture grouping ----"
IR_START = "// ---- ir grouping ----"
IR_END = "// ---- end ir grouping ----"


def grouping_source():
    """The real block from plugin.js, because the desktop runtime forbids sibling imports."""
    face = (ROOT / "desktop" / "plugin.js").read_text(encoding="utf-8")
    if START not in face or END not in face:
        raise AssertionError("capture grouping markers are missing from desktop/plugin.js")
    return face.split(START, 1)[1].split(END, 1)[0]


def ir_source():
    """The cabinet block plus the small name helpers above it, taken from the face."""
    face = (ROOT / "desktop" / "plugin.js").read_text(encoding="utf-8")
    if IR_START not in face or IR_END not in face:
        raise AssertionError("ir grouping markers are missing from desktop/plugin.js")
    return grouping_source() + face.split(IR_START, 1)[1].split(IR_END, 1)[0]


def run_tree(rows, source=None, name="captureTree"):
    """Run a grouping block from the face under node, so the test cannot drift from the view."""
    with tempfile.TemporaryDirectory() as tmp:
        module = Path(tmp) / "grouping.mjs"
        module.write_text((source or grouping_source()) + f"\nexport {{ {name} }};\n", encoding="utf-8")
        script = (
            f"import {{{name}}} from {json.dumps(module.as_uri())}; "
            f"const rows = {json.dumps(rows)}; "
            f"console.log(JSON.stringify({name}(rows)));"
        )
        out = subprocess.run(
            [NODE, "--input-type=module", "-e", script],
            capture_output=True, text=True, timeout=30, check=True
        )
    return json.loads(out.stdout)


@unittest.skipUnless(NODE, "node is needed to execute the desktop grouping module")
class TestCaptureGrouping(unittest.TestCase):
    rows = [
        {"name": "PEAVEY - 6506 - CLEAN - B0 - HIGH - G1.0", "folder": "Peavey 6505", "path": "a"},
        {"name": "PEAVEY - 6506 - CRUNCH - B1 - NORMAL - G2.0", "folder": "Peavey 6505", "path": "b"},
        {"name": "PEAVEY - 6506 - RED - B0 - HIGH - G3.0", "folder": "Peavey 6505", "path": "c"},
        {"name": "01 Morning Wood", "folder": "HSCC", "path": "d"},
        {"name": "[AMP] PRS-MT100 CLEAN CCV DI - STD", "folder": "PRS MT100 Mark Tremonti", "path": "e"},
    ]

    def test_brand_then_model_then_variant(self):
        tree = run_tree(self.rows)
        peavey = next(brand for brand in tree if brand["label"] == "Peavey")
        self.assertEqual(peavey["count"], 3)
        model = peavey["models"][0]
        self.assertEqual(model["label"], "6505")
        self.assertEqual([v["label"] for v in model["variants"]], ["CLEAN", "CRUNCH", "RED"])
        self.assertEqual([v["count"] for v in model["variants"]], [1, 1, 1])

    def test_rows_lose_the_words_the_headers_already_say(self):
        tree = run_tree(self.rows)
        peavey = next(brand for brand in tree if brand["label"] == "Peavey")
        clean = peavey["models"][0]["variants"][0]
        self.assertEqual(clean["items"][0]["label"], "6506 · B0 · HIGH · G1.0")

    def test_a_folder_without_a_variant_stays_a_flat_list(self):
        tree = run_tree(self.rows)
        hscc = next(brand for brand in tree if brand["label"] == "HSCC")
        model = hscc["models"][0]
        self.assertEqual(model["variants"], [])
        self.assertEqual([item["name"] for item in model["items"]], ["01 Morning Wood"])

    def test_no_capture_is_lost_or_duplicated(self):
        tree = run_tree(self.rows)
        found = []
        for brand in tree:
            for model in brand["models"]:
                found.extend(item["path"] for item in model["items"])
                for variant in model["variants"]:
                    found.extend(item["path"] for item in variant["items"])
        self.assertEqual(sorted(found), sorted(row["path"] for row in self.rows))

    def test_brands_are_alphabetical(self):
        tree = run_tree(self.rows)
        labels = [brand["label"] for brand in tree]
        self.assertEqual(labels, sorted(labels, key=str.casefold))


@unittest.skipUnless(NODE, "node is needed to execute the desktop grouping module")
class TestIrGrouping(unittest.TestCase):
    rows = [
        {"name": "BlackBack 440", "folder": "HSCC Cabs", "path": "a"},
        {"name": "BlackBack 57", "folder": "HSCC Cabs", "path": "b"},
        {"name": "BlackBack Board", "folder": "HSCC Cabs", "path": "c"},
        {"name": "Cali 57", "folder": "HSCC Cabs", "path": "d"},
        {"name": "G12-65 blend 1", "folder": "Scharrface", "path": "e"},
        {"name": "G12-65 blend 2", "folder": "Scharrface", "path": "f"},
        {"name": "G12T-75 1984_ 1991 57 and 7B blend 1", "folder": "Scharrface", "path": "g"},
        {"name": "Bark", "folder": "Devil's Lab", "path": "h"},
        {"name": "Mutt", "folder": "Devil's Lab", "path": "i"},
        {"name": "AURORA_212_GOV", "folder": "FenrIR / Factory IRs", "path": "j"},
        {"name": "AURORA_212_WIZ", "folder": "FenrIR / Factory IRs", "path": "k"},
        {"name": "Aurora412_Scooped", "folder": "FenrIR / Factory IRs", "path": "l"},
        {"name": "Aurora412_Tight", "folder": "FenrIR / Factory IRs", "path": "m"},
        {"name": "Res Barefoot 1", "folder": "Barefoot IR Pack / Barefoot IRs", "path": "n"},
        {"name": "Res Barefoot 2", "folder": "Barefoot IR Pack / Barefoot IRs", "path": "o"},
    ]

    def tree(self):
        return run_tree(self.rows, ir_source(), "irTree")

    def pack(self, label):
        return next(pack for pack in self.tree() if pack["label"] == label)

    def test_pack_then_speaker(self):
        hscc = self.pack("HSCC Cabs")
        self.assertEqual(hscc["count"], 4)
        blackback = next(model for model in hscc["models"] if model["label"] == "BlackBack")
        self.assertEqual(blackback["count"], 3)
        self.assertEqual([item["label"] for item in blackback["items"]], ["440", "57", "Board"])
        # Cali has no siblings, so it is a row under the pack, not a group of one.
        self.assertEqual([item["label"] for item in hscc["items"]], ["Cali 57"])

    def test_speaker_is_read_before_the_first_microphone(self):
        scharr = self.pack("Scharrface")
        g12 = next(model for model in scharr["models"] if model["label"] == "G12-65")
        self.assertEqual(g12["count"], 2)
        self.assertEqual([item["label"] for item in g12["items"]], ["blend 1", "blend 2"])

    def test_a_pack_of_one_offs_is_a_flat_list(self):
        devils = self.pack("Devil's Lab")
        self.assertEqual(devils["models"], [])
        self.assertEqual(sorted(item["name"] for item in devils["items"]), ["Bark", "Mutt"])

    def test_underscored_cabinet_names_group(self):
        factory = self.pack("FenrIR / Factory IRs")
        labels = [model["label"] for model in factory["models"]]
        self.assertEqual(labels, ["AURORA", "Aurora412"])
        self.assertEqual([model["count"] for model in factory["models"]], [2, 2])

    def test_a_pack_with_one_cabinet_has_nothing_to_fold(self):
        barefoot = self.pack("Barefoot IR Pack / Barefoot IRs")
        self.assertEqual(barefoot["models"], [])
        self.assertEqual([item["label"] for item in barefoot["items"]], ["Res Barefoot 1", "Res Barefoot 2"])

    def test_no_cab_is_lost_or_duplicated(self):
        found = []
        for pack in self.tree():
            found.extend(item["path"] for item in pack["items"])
            for model in pack["models"]:
                found.extend(item["path"] for item in model["items"])
        self.assertEqual(sorted(found), sorted(row["path"] for row in self.rows))

    def test_packs_are_alphabetical(self):
        labels = [pack["label"] for pack in self.tree()]
        self.assertEqual(labels, sorted(labels, key=str.casefold))


if __name__ == "__main__":
    unittest.main()
