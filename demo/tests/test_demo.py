"""End-to-end test of the synthetic demo (demo/run_demo.py). Pins the numbers shown in the README screenshot."""
import contextlib
import csv
import io
import sys
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "demo"))
import make_demo_data
import run_demo


class DemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls._tmp.name) / "demo"
        with contextlib.redirect_stdout(io.StringIO()):
            cls.code = run_demo.main(["--output", str(cls.out)])  # every step must succeed, incl. the leak scan
        cls.extracted = cls.out / "extract"

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def rows(self, name):
        with (self.extracted / name).open(encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))

    def test_pipeline_runs_and_review_step_is_exercised(self):
        self.assertEqual(self.code, 0)
        self.assertTrue((self.out / run_demo.WORKBOOK_NAME).is_file())
        claims = self.rows("claims_anonymized.csv")
        self.assertEqual(len(claims), len(make_demo_data.MEDICAL) + len(make_demo_data.PHARMACY)
                         + len(make_demo_data.DENTAL))
        self.assertEqual(sum(r["allowed_basis"] == "manually_verified" for r in claims), 2)
        self.assertEqual(sum(r["source"] == "claims_csv+eob_pdf" for r in claims),
                         sum(c[-1] for c in make_demo_data.MEDICAL))
        self.assertEqual({r["member_tag"] for r in claims}, {"Member_A", "Member_B", "Member_C", "Member_D"})

    def test_headline_numbers_match_the_published_screenshot(self):
        # If these change, re-run demo/make_screenshot.py and update docs/images and the README figures.
        h = run_demo.headline(self.extracted / "claims_anonymized.csv")
        self.assertEqual(h["year"], 2025)
        self.assertEqual(h["net"], {"example_ppo": D("7785.58"), "example_hdhp": D("2995.43")})
        (baseline,) = h["baseline"]
        self.assertEqual((baseline["actual"], baseline["modeled"], baseline["passes"]), (D("1329.00"), D("1329.00"), True))

    def test_no_synthetic_identifiers_in_output_csvs(self):
        text = "".join(p.read_text(encoding="utf-8-sig") for p in self.extracted.glob("*.csv"))
        secrets = {"Demo", "Alex", "Blair", "Casey", "Drew", "Maple", "Riverbend", "Northside", "Harbor", "Lakeside",
                   "Bright Smile", "Corner Pharmacy", "Demostatin", "Exampleair", "Amoxidemo", "RX-0001",
                   make_demo_data.MEMBER_ID, make_demo_data.CLAIM_PREFIX, "Sampleton", "2025-01-14", "01/14/2025"}
        for secret in secrets:
            self.assertNotIn(secret, text)

    def test_refuses_to_wipe_a_folder_it_did_not_create(self):
        with tempfile.TemporaryDirectory() as t:
            keep = Path(t) / "my_claims.csv"
            keep.write_text("private", encoding="utf-8")
            with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()):
                run_demo.main(["--output", t])
            self.assertTrue(keep.is_file())


if __name__ == "__main__":
    unittest.main()
