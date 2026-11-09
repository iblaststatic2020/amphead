import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

NODE = shutil.which("node")
ROOT = Path(__file__).resolve().parents[1]


def run_tree(rows):
    """Run the real desktop grouping module under node, so the test cannot drift from the face."""
    source = ROOT / "desktop" / "captures.js"
    with tempfile.TemporaryDirectory() as tmp:
        module = Path(tmp) / "captures.mjs"
        shutil.copyfile(source, module)
        script = (
            f"import {{captureTree}} from {json.dumps(module.as_uri())}; "
            f"const rows = {json.dumps(rows)}; "
            "console.log(JSON.stringify(captureTree(rows)));"
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


if __name__ == "__main__":
    unittest.main()
