"""Tests for the data-intake tools (profile_table.py, convert_claims.py). Synthetic data only."""
import argparse
import csv
import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(ROOT / "cigna_eob_local"))
import convert_claims as cc
import profile_table as pt
import cigna_eob_extract as eob

HEADER = ["DOS", "Member Name", "Doctor", "Claim Type", "Charges", "Paid by Plan", "You Owe", "Claim ID"]
ROWS = [
    ["2025-03-04", "Robin Hiddenname", "Dr Secret Provider", "Medical", "$1,200.00", "$900.00", "$300.00", "CLX88231"],
    ["2025-04-05", "Robin Hiddenname", "Dr Secret Provider", "Pharmacy", "$30.00", "$20.00", "$10.00", "CLX88232"],
    ["2025-05-06", "Sky Hiddenname", "Hidden Lab", "medical", "(25.00)", "", "-25.00", "CLX88233"],
    ["", "Sky Hiddenname", "Hidden Lab", "Medical", "$10.00", "$5.00", "$5.00", "CLX88234"],
]
SECRETS = ("Hiddenname", "Robin", "Sky", "Secret Provider", "Hidden Lab", "CLX88231", "1,200")


def write(path, header, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def run_convert(**kw):
    defaults = dict(kind="medical", input=None, output=None, map=[], set=[], where=[], sheet=None, skip_rows=0,
                    date_format=None, patient_ids_to_names=False, template=False, force=False)
    defaults.update(kw)
    return cc.convert(argparse.Namespace(**defaults))


class IntakeToolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.src = self.root / "export.csv"
        write(self.src, HEADER, ROWS)

    def tearDown(self):
        self._tmp.cleanup()

    def test_profile_reports_shapes_but_never_values(self):
        out = subprocess.run([sys.executable, str(TOOLS / "profile_table.py"), str(self.src)],
                             capture_output=True, text=True, check=True).stdout
        self.assertIn("'Charges': money", out)
        self.assertIn("'DOS': date", out)
        self.assertIn("range 2025-03..2025-05", out)
        self.assertIn("may contain personal names", out)
        self.assertIn("looks like an identifier", out)
        for secret in SECRETS:
            self.assertNotIn(secret, out)

    def test_show_values_only_for_safe_categorical_columns(self):
        ok = subprocess.run([sys.executable, str(TOOLS / "profile_table.py"), str(self.src), "--show-values", "Claim Type"],
                            capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0)
        self.assertIn("Pharmacy", ok.stdout)
        refused = subprocess.run([sys.executable, str(TOOLS / "profile_table.py"), str(self.src), "--show-values",
                                  "Member Name"], capture_output=True, text=True)
        self.assertEqual(refused.returncode, 1)
        self.assertNotIn("Robin", refused.stdout)

    def test_title_row_header_is_hidden(self):
        src = self.root / "titled.csv"
        write(src, ["Claims for Robin Hiddenname member M123456 printed 2025", "", ""],
              [["DOS", "Charges", "You Owe"], ["2025-01-02", "10.00", "5.00"]])
        out = subprocess.run([sys.executable, str(TOOLS / "profile_table.py"), str(src)],
                             capture_output=True, text=True, check=True).stdout
        self.assertNotIn("Hiddenname", out)
        self.assertIn("--skip-rows", out)

    def test_convert_maps_filters_and_is_read_by_the_extractor(self):
        out = self.root / "medical.csv"
        result = run_convert(input=self.src, output=out, where=["Claim Type=MEDICAL"], set=["Status=Processed"],
                             map=["Service Date=DOS", "Patient=Member Name", "Provider=Doctor", "Billed=Charges",
                                  "Plan Paid=Paid by Plan", "Patient Responsibility=You Owe"])
        self.assertEqual((result["kept"], result["written"]), (3, 2))
        self.assertEqual(result["skipped"], {"unreadable or missing date": 1})
        self.assertEqual(result["notes"], {"blank Plan Paid read as $0.00": 1})
        kind, rows = eob.read_claims_csv(out)
        self.assertEqual(kind, "medical")
        self.assertEqual([r["Service Date"] for r in rows], ["03/04/2025", "05/06/2025"])
        self.assertEqual(rows[1]["Billed"], "-25.00")
        self.assertNotIn("Claim ID", rows[0])

    def test_convert_requires_every_layout_column_and_refuses_overwrite(self):
        out = self.root / "medical.csv"
        with self.assertRaises(ValueError):
            run_convert(input=self.src, output=out, map=["Service Date=DOS"])
        with self.assertRaises(ValueError):
            run_convert(input=self.src, output=out, set=["Status=Processed"],
                        map=["Service Date=DOS", "Patient=Nope", "Billed=Charges", "Plan Paid=Paid by Plan",
                             "Patient Responsibility=You Owe"])
        out.write_text("x", encoding="utf-8")
        with self.assertRaises(ValueError):
            run_convert(template=True, output=out)

    def test_ambiguous_day_month_dates_need_a_format(self):
        src = self.root / "eu.csv"
        write(src, ["Date", "Who", "Billed", "Paid", "Owed"], [["03/04/2025", "Pat", "10", "5", "5"],
                                                               ["05/06/2025", "Pat", "10", "5", "5"]])
        args = dict(input=src, set=["Status=Processed"],
                    map=["Service Date=Date", "Patient=Who", "Billed=Billed", "Plan Paid=Paid", "Patient Responsibility=Owed"])
        with self.assertRaises(ValueError):
            run_convert(output=self.root / "a.csv", **args)
        run_convert(output=self.root / "b.csv", date_format="%d/%m/%Y", **args)
        _, rows = eob.read_claims_csv(self.root / "b.csv")
        self.assertEqual(rows[0]["Service Date"], "04/03/2025")

    def test_member_ids_become_distinct_letter_tokens(self):
        self.assertEqual(cc.ids_to_names("M-0421"), "Maecb")
        self.assertNotEqual(eob.member_key(cc.ids_to_names("M0421")), eob.member_key(cc.ids_to_names("M0422")))

    def test_template_writes_the_layout_header(self):
        out = self.root / "pharmacy.csv"
        run_convert(kind="pharmacy", template=True, output=out)
        self.assertEqual(out.read_text(encoding="utf-8").strip(), ",".join(cc.LAYOUTS["pharmacy"]))

    def test_cli_prints_counts_only(self):
        out = self.root / "medical.csv"
        buf = io.StringIO()
        argv = ["convert_claims.py", "--kind", "medical", "--input", str(self.src), "--output", str(out),
                "--set", "Status=Processed", "--map", "Service Date=DOS", "--map", "Patient=Member Name",
                "--map", "Billed=Charges", "--map", "Plan Paid=Paid by Plan", "--map", "Patient Responsibility=You Owe"]
        old = sys.argv
        try:
            sys.argv = argv
            with redirect_stdout(buf):
                self.assertEqual(cc.main(), 0)
        finally:
            sys.argv = old
        text = buf.getvalue()
        self.assertIn("written 3", text)
        for secret in SECRETS:
            self.assertNotIn(secret, text)


if __name__ == "__main__":
    unittest.main()
