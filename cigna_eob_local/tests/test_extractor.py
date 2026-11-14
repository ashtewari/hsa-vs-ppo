"""Synthetic regression tests. No real EOBs, claims, or network required."""
import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymupdf
import cigna_eob_extract as eob

FIXTURES = Path(__file__).resolve().parents[2] / "cigna_eob_synthetic_test_data"


def fake_eob(path, date, billed, discount, not_covered, paid, owed, ident="SAMPLE TEST PERSON",
             reference=True, not_covered_label="Amount not covered", extra_lines=()):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((50, 40), "Cigna Explanation of Benefits")
    page.insert_text((50, 70), f"Summary of a claim for services on {date}")
    page.insert_text((50, 90), f"For: {ident}" + (" - Reference # 998877665544" if reference else ""))
    for i, text in enumerate(extra_lines):
        page.insert_text((50, 110 + i * 12), text, fontsize=8)
    items = [("Amount billed", billed), ("Discount", discount), (not_covered_label, not_covered),
             ("What Cigna plan paid", paid), ("What I owe", owed)]
    for i, (label, money) in enumerate(items):
        page.insert_text((50, 160 + i*35), label)
        page.insert_text((340, 160 + i*35), f"${money:.2f}")
    doc.save(path)
    doc.close()


def fake_cigna_layout_eob(path, billed, reduction, paid, owed, network="IN NETWORK"):
    """Mimics the real Cigna layout: cover page first, wrapped labels, no 'not covered' row when zero."""
    doc = pymupdf.open()
    cover = doc.new_page(width=612, height=792)
    cover.insert_text((65, 263), "Explanation of payment. The payment is for the claim below and is not a bill.")
    page = doc.new_page(width=612, height=792)
    page.insert_text((71, 140), "Patient: SECRET LAYOUT PERSON", fontsize=9)
    page.insert_text((405, 210), "Service date", fontsize=9)
    page.insert_text((405, 223), "March 3, 2026", fontsize=9)
    page.insert_text((405, 271), "Provider Network Status:", fontsize=9)
    page.insert_text((405, 281), network, fontsize=9)
    page.insert_text((71, 438), "Summary of a claim for services on March 3, 2026", fontsize=9)
    page.insert_text((71, 497), "Amount Billed", fontsize=9)
    page.insert_text((175, 497), f"${billed:.2f}", fontsize=11)
    page.insert_text((71, 521), "Cost Reduction", fontsize=9)
    page.insert_text((175, 521), f"${reduction:.2f}", fontsize=11)
    page.insert_text((71, 578), "What your plan", fontsize=9)
    page.insert_text((71, 591), "paid", fontsize=9)
    page.insert_text((180, 585), f"${paid:.2f}", fontsize=11)
    page.insert_text((231, 600), "plan paid $1.23 is an explanatory sentence, not a label.", fontsize=9)
    page.insert_text((71, 639), "What I owe", fontsize=9)
    page.insert_text((180, 639), f"${owed:.2f}", fontsize=11)
    doc.save(path)
    doc.close()


def write_rows(path, header, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def read_csv(path):
    with path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        if "issue_codes" in r:
            assert set(filter(None, r["issue_codes"].split(";"))) <= set(eob.ISSUE_CODES), r["issue_codes"]
    return rows


def all_output_text(out):
    return "\n".join(p.read_text(encoding="utf-8-sig") for p in out.glob("*.csv"))


class EOBLocalTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.inp = root / "input"; self.inp.mkdir()
        self.out = root / "output"; self.out.mkdir()
        self.root = root

    def tearDown(self):
        self._tmp.cleanup()

    def test_good_claim_and_no_pii_exports(self):
        fake_eob(self.inp / "private_jane_doeeob.pdf", "November 9, 2025", 189, 70.05, 0, 107.06, 11.89,
                 ident="TEST PERSON SECRET",
                 extra_lines=("DOB 01/02/1980", "12 Hidden Lane, Secretville", "Provider: Dr Hidden Provider"))
        eob.extract(self.inp, self.out)
        rows = read_csv(self.out / "claims_anonymized.csv")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["allowed_estimated"], "118.95")
        self.assertEqual(rows[0]["service_month"], "2025-11")
        self.assertEqual(rows[0]["member_owes"], "11.89")
        combined = all_output_text(self.out)
        for secret in ("TEST PERSON SECRET", "998877665544", "private_jane", "November 9", "01/02/1980",
                       "Hidden Lane", "Secretville", "Hidden Provider"):
            self.assertNotIn(secret, combined)

    def test_irreconcilable_claim_is_excluded(self):
        fake_eob(self.inp / "bad.pdf", "January 10, 2026", 400, 100, 0, 70, 90)
        eob.extract(self.inp, self.out)
        self.assertEqual(len(read_csv(self.out / "claims_anonymized.csv")), 0)
        row = read_csv(self.out / "review_candidates.csv")[0]
        self.assertEqual(row["approved"], "no")
        self.assertIn("paid_plus_owed_not_reconciled", row["issue_codes"])

    def test_duplicate_pdf_excluded(self):
        fake_eob(self.inp / "first.pdf", "January 10, 2026", 400, 100, 0, 250, 50)
        (self.inp / "second.pdf").write_bytes((self.inp / "first.pdf").read_bytes())
        eob.extract(self.inp, self.out)
        self.assertEqual(len(read_csv(self.out / "claims_anonymized.csv")), 0)
        rows = read_csv(self.out / "review_candidates.csv")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all("possible_reprocessed_claim" in r["issue_codes"] for r in rows))
        self.assertFalse(any(r["issue_codes"].startswith(";") for r in rows))

    def test_identical_pdf_without_reference_flagged_by_hash(self):
        fake_eob(self.inp / "first.pdf", "January 10, 2026", 400, 100, 0, 250, 50, reference=False)
        (self.inp / "second.pdf").write_bytes((self.inp / "first.pdf").read_bytes())
        eob.extract(self.inp, self.out)
        first, second = read_csv(self.out / "review_candidates.csv")
        self.assertEqual(first["issue_codes"], "")
        self.assertEqual(second["issue_codes"], "duplicate_pdf")

    def test_image_only_pdf_is_flagged_as_scanned(self):
        doc = pymupdf.open()
        page = doc.new_page(width=612, height=792)
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 60), False)
        pix.clear_with(180)
        page.insert_image(pymupdf.Rect(50, 50, 550, 700), pixmap=pix)
        doc.save(self.inp / "scan.pdf")
        doc.close()
        eob.extract(self.inp, self.out)
        row = read_csv(self.out / "review_candidates.csv")[0]
        self.assertTrue(row["issue_codes"].startswith("scanned_or_no_selectable_text"))

    def test_not_covered_amount_label_is_not_read_as_allowed(self):
        fake_eob(self.inp / "nc.pdf", "January 10, 2026", 400, 100, 25, 250, 75, not_covered_label="Not covered amount")
        eob.extract(self.inp, self.out)
        row = read_csv(self.out / "review_candidates.csv")[0]
        self.assertEqual(row["not_covered"], "25.00")
        self.assertEqual(row["allowed_estimated"], "275.00")
        self.assertEqual(row["allowed_basis"], "derived_from_billed_discount_notcovered")

    def test_cigna_layout_wrapped_labels_network_and_omitted_not_covered(self):
        fake_cigna_layout_eob(self.inp / "eob.pdf", 250, 100, 120, 30)
        eob.extract(self.inp, self.out)
        row = read_csv(self.out / "review_candidates.csv")[0]
        self.assertEqual(row["issue_codes"], "")
        self.assertEqual(row["service_month"], "2026-03")
        self.assertEqual(row["not_covered"], "0.00")
        self.assertEqual(row["allowed_estimated"], "150.00")
        self.assertEqual(row["plan_paid"], "120.00")
        self.assertEqual(row["network"], "in_network")
        self.assertNotIn("SECRET LAYOUT PERSON", all_output_text(self.out))

    def test_not_covered_excluded_from_member_owes_gets_specific_code(self):
        fake_eob(self.inp / "nc.pdf", "January 10, 2026", 400, 100, 25, 250, 25)
        eob.extract(self.inp, self.out)
        row = read_csv(self.out / "review_candidates.csv")[0]
        self.assertIn("noncovered_not_in_member_owes", row["issue_codes"])
        self.assertNotIn("paid_plus_owed_not_reconciled", row["issue_codes"])

    def test_extract_refuses_to_overwrite_review_file_unless_forced(self):
        fake_eob(self.inp / "a.pdf", "January 10, 2026", 400, 100, 0, 250, 50)
        eob.extract(self.inp, self.out)
        review = self.out / "review_candidates.csv"
        review.write_text(review.read_text(encoding="utf-8-sig") + "manual edit marker\n", encoding="utf-8-sig")
        with self.assertRaises(ValueError):
            eob.extract(self.inp, self.out)
        self.assertIn("manual edit marker", review.read_text(encoding="utf-8-sig"))
        eob.extract(self.inp, self.out, force=True)
        backups = list(self.out.glob("review_candidates.backup-*.csv"))
        self.assertEqual(len(backups), 1)
        self.assertIn("manual edit marker", backups[0].read_text(encoding="utf-8-sig"))
        self.assertNotIn("manual edit marker", review.read_text(encoding="utf-8-sig"))

    def test_claims_csv_import_with_pdf_match(self):
        fake_eob(self.inp / "match.pdf", "January 10, 2026", 400, 100, 0, 250, 50, ident="TEST PERSON SECRET")
        medical = self.root / "Claim_Summary_medical.csv"
        write_rows(medical, ["Service Date", "Patient", "Provider", "Status", "Billed", "Plan Paid",
                             "Patient Responsibility", "I Owe", "My Payments"], [
            ["01/10/2026", "Pat Secretname", "Dr Hidden Provider", "Processed", "$400.00", "$250.00", "$50.00", "$0.00", "$50.00"],
            ["02/03/2026", "Robin Secretname", "Secret Labs Inc", "Processed", "$120.00", "$80.00", "$20.00", "$20.00", "$0.00"],
            ["03/04/2026", "Pat Secretname", "Dr Hidden Provider", "Denied", "$90.00", "$0.00", "$0.00", "$0.00", "$0.00"],
        ])
        dental = self.root / "Claim_Summary_dental.csv"
        write_rows(dental, ["Service Date", "Patient", "Provider", "Status", "Billed", "Not Allowed", "Covered Amount",
                            "Contracted Amount", "Remaining Balance", "Plan Paid", "Not Covered By Plan",
                            "Applied To Deductible", "Patient Responsibility", "My Payments"], [
            ["05/06/2025", "Robin", "Hidden Dental Group", "Processed", "$300.00", "$0.00", "$180.00", "$180.00",
             "$36.00", "$144.00", "$0.00", "$0.00", "$36.00", "$0.00"],
        ])
        pharmacy = self.root / "Claim_Summary_pharmacy.csv"
        write_rows(pharmacy, ["Fill Date", "Rx Number", "Drug Name", "Pharmacy Name", "Prescribed By", "For",
                              "Amount Billed", "My Cigna Plan Paid", "Patient Responsibility"], [
            ["07/08/2025", "7654321", "Secretdrugname 10mg", "Hidden Pharmacy", "Dr Prescriber Secret", "Pat",
             "$25.00", "$15.00", "$10.00"],
        ])
        eob.extract(self.inp, self.out, claims_csvs=[medical, dental, pharmacy])
        rows = {r["eob_id"]: r for r in read_csv(self.out / "review_candidates.csv")}
        dent, matched, unmatched, denied, rx = (rows[f"CLM_0000{i}"] for i in range(1, 6))

        self.assertEqual((matched["source"], matched["issue_codes"], matched["allowed_estimated"]),
                         ("claims_csv+eob_pdf", "", "300.00"))
        self.assertEqual((unmatched["allowed_basis"], unmatched["issue_codes"], unmatched["category"]),
                         ("derived_from_paid_plus_owed", "no_matching_eob_pdf", "laboratory"))
        self.assertIn("claim_denied", denied["issue_codes"])
        self.assertEqual((dent["coverage_type"], dent["discount"], dent["allowed_estimated"], dent["issue_codes"]),
                         ("dental", "120.00", "180.00", ""))
        self.assertEqual((rx["coverage_type"], rx["allowed_estimated"], rx["issue_codes"]), ("pharmacy", "25.00", ""))
        self.assertEqual({matched["member_tag"], denied["member_tag"], rx["member_tag"]}, {"Member_A"})
        self.assertEqual({unmatched["member_tag"], dent["member_tag"]}, {"Member_B"})

        summary = read_csv(self.out / "annual_summary.csv")
        self.assertEqual({(r["year"], r["coverage_type"]) for r in summary},
                         {("2025", "dental"), ("2025", "pharmacy"), ("2026", "medical")})
        combined = all_output_text(self.out)
        for secret in ("Secretname", "Pat", "Robin", "Hidden Provider", "Secret Labs", "Hidden Dental", "Secretdrugname",
                       "7654321", "Hidden Pharmacy", "Prescriber", "01/10/2026", "TEST PERSON SECRET", "998877665544",
                       "match.pdf", "Claim_Summary"):
            self.assertNotIn(secret, combined)

    def test_unrecognised_claims_csv_is_rejected(self):
        other = self.root / "other.csv"
        write_rows(other, ["Date", "Amount"], [["01/01/2026", "1.00"]])
        with self.assertRaises(ValueError):
            eob.extract(None, self.out, claims_csvs=[other])

    def test_parse_money_keeps_minus_sign_in_any_position(self):
        for text in ("-25.00", "$-25.00", "-$25.00", "25.00-", "($25.00)", "$(25.00)"):
            self.assertEqual(eob.parse_money(text), eob.Decimal("-25.00"), text)
        self.assertEqual(eob.parse_money("$1,234.5"), eob.Decimal("1234.50"))
        for text in ("($25.00", "12-34", "abc", "$"):
            self.assertIsNone(eob.parse_money(text), text)

    def test_csv_reversal_row_is_not_auto_approved(self):
        pharmacy = self.root / "Claim_Summary_pharmacy.csv"
        write_rows(pharmacy, ["Fill Date", "Rx Number", "Drug Name", "For", "Amount Billed", "My Cigna Plan Paid",
                              "Patient Responsibility"], [["07/08/2025", "1", "X", "Pat", "$-25.00", "$-15.00", "$-10.00"]])
        eob.extract(None, self.out, claims_csvs=[pharmacy])
        row = read_csv(self.out / "review_candidates.csv")[0]
        self.assertEqual(row["approved"], "no")
        self.assertIn("negative_or_adjusted_claim", row["issue_codes"])

    def test_csv_with_trailing_empty_field_is_read_and_extra_values_rejected(self):
        medical = self.root / "Claim_Summary_medical.csv"
        header = "Service Date,Patient,Provider,Status,Billed,Plan Paid,Patient Responsibility\n"
        medical.write_text(header + "01/10/2026,Pat X,Dr Y,Processed,$400.00,$250.00,$50.00,\n", encoding="utf-8")
        kind, rows = eob.read_claims_csv(medical)
        self.assertEqual((kind, rows[0]["Patient Responsibility"]), ("medical", "$50.00"))
        medical.write_text(header + "01/10/2026,Pat X,Dr Y,Processed,$400.00,$250.00,$50.00,oops\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            eob.read_claims_csv(medical)

    def test_estimates_expand_and_are_reported_separately(self):
        fake_eob(self.inp / "a.pdf", "January 10, 2026", 400, 100, 0, 250, 50)
        est = self.root / "estimates.csv"
        write_rows(est, eob.ESTIMATE_FIELDS, [
            ["Member_B", "pharmacy", "pharmacy", "2025-11", "2026-02", "1", "15.00", "5.00", "10.00"],
            ["", "vision", "vision", "2025-01", "2026-01", "12", "400.00", "130.00", "270.00"],
        ])
        eob.extract(self.inp, self.out, estimates=est)
        accepted = read_csv(self.out / "claims_anonymized.csv")
        estimates = [r for r in accepted if r["source"] == "estimate"]
        self.assertEqual(len(estimates), 6)
        self.assertTrue(all(r["review_status"] == "estimate" and r["allowed_basis"] == "estimated" for r in estimates))
        self.assertEqual(sorted(r["service_month"] for r in estimates if r["coverage_type"] == "vision"),
                         ["2025-01", "2026-01"])
        summary = {(r["year"], r["coverage_type"], r["data_basis"]): r for r in read_csv(self.out / "annual_summary.csv")}
        self.assertEqual(summary[("2026", "pharmacy", "estimate")]["member_responsibility"], "20.00")
        self.assertEqual(summary[("2026", "medical", "actual")]["accepted_claims"], "1")

    def test_estimates_that_do_not_reconcile_are_rejected(self):
        fake_eob(self.inp / "a.pdf", "January 10, 2026", 400, 100, 0, 250, 50)
        est = self.root / "estimates.csv"
        write_rows(est, eob.ESTIMATE_FIELDS, [["Member_B", "pharmacy", "pharmacy", "2025-11", "2026-02", "1",
                                               "15.00", "5.00", "9.00"]])
        with self.assertRaises(ValueError):
            eob.extract(self.inp, self.out, estimates=est)

    def test_leak_scan_passes_clean_outputs_and_catches_a_planted_leak(self):
        import leak_scan
        fake_eob(self.inp / "a.pdf", "January 10, 2026", 400, 100, 0, 250, 50)
        claims = self.root / "Claim_Summary_medical.csv"
        write_rows(claims, ["Service Date", "Patient", "Provider", "Status", "Billed", "Plan Paid",
                            "Patient Responsibility"], [
            ["01/10/2026", "Pat Secretname", "Urgent Care Hidden Clinic", "Processed", "$400.00", "$250.00", "$50.00"]])
        eob.extract(self.inp, self.out, claims_csvs=[claims])
        secrets = leak_scan.collect_secrets([claims], self.inp, None)
        self.assertEqual(sum(found for _, found in leak_scan.scan(self.out, secrets).values()), 0)
        with (self.out / "claims_anonymized.csv").open("a", encoding="utf-8-sig") as f:
            f.write("planted,Secretname,998877665544\n")
        results = leak_scan.scan(self.out, secrets)
        self.assertEqual(results["names"][1], 1)
        self.assertEqual(results["claim_numbers"][1], 1)

    def test_synthetic_fixtures_reproduce_expected_outputs(self):
        cases = sorted(p.parent for p in FIXTURES.rglob("expected_output"))
        self.assertGreaterEqual(len(cases), 4)
        for case in cases:
            with self.subTest(case=case.name), tempfile.TemporaryDirectory() as t:
                eob.extract(case / "input", Path(t))
                for expected in sorted((case / "expected_output").glob("*.csv")):
                    self.assertEqual((Path(t) / expected.name).read_bytes(), expected.read_bytes(), expected.name)


if __name__ == "__main__":
    unittest.main()
