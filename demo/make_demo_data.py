#!/usr/bin/env python3
"""Write the demo's synthetic inputs: myCigna-style claim CSVs, EOB PDFs and a leak-terms file.

Everything here is fictional: the "Demo" family, providers, drugs and IDs are made up, and the PDFs are
stamped "SYNTHETIC DEMO - not a Cigna document". Amounts are hand-set so that replaying them under the
example PPO in plan_parameters/plan_parameters.example.json reproduces what was owed (the baseline check).

Usage:  python demo/make_demo_data.py --output demo/output/inputs
"""
from __future__ import annotations

import argparse
import csv
from datetime import date
from pathlib import Path

import pymupdf

STAMP = "SYNTHETIC DEMO - not a Cigna document"
MEMBER_ID = "DEMO-0042"
ADDRESS = "42 Example Lane, Sampleton"
CLAIM_PREFIX = "4417"  # joined with a 6-digit counter so the EOBs carry claim numbers the leak scan checks

# date, patient, provider, service (EOB text), billed, allowed, plan paid, patient responsibility, has EOB PDF
MEDICAL = [
    (date(2025, 1, 14), "Alex Demo", "Maple Family Medicine", "Annual physical", 320, 210, 210, 0, True),
    (date(2025, 1, 14), "Alex Demo", "Riverbend Lab", "Lab test", 190, 95, 76, 19, True),
    (date(2025, 2, 20), "Casey Demo", "Maple Family Medicine", "Office visit", 210, 140, 115, 25, True),
    (date(2025, 3, 8), "Casey Demo", "Northside Urgent Care", "Urgent care visit", 285, 175, 125, 50, True),
    (date(2025, 3, 22), "Blair Demo", "Harbor Orthopedics", "Office visit", 410, 240, 190, 50, True),
    (date(2025, 4, 5), "Blair Demo", "Lakeside Imaging", "MRI", 2400, 1150, 520, 630, True),
    (date(2025, 4, 19), "Blair Demo", "Harbor Orthopedics", "Office visit", 395, 230, 180, 50, True),
    (date(2025, 5, 12), "Drew Demo", "Maple Family Medicine", "Preventive visit", 260, 180, 180, 0, True),
    (date(2025, 6, 3), "Blair Demo", "Maple Family Medicine", "Annual physical", 320, 210, 210, 0, True),
    (date(2025, 7, 16), "Drew Demo", "Northside Urgent Care", "Urgent care visit", 265, 165, 115, 50, True),
    (date(2025, 7, 16), "Drew Demo", "Lakeside Imaging", "X-ray", 240, 160, 128, 32, True),
    (date(2025, 9, 9), "Alex Demo", "Maple Family Medicine", "Office visit", 205, 135, 110, 25, False),
    (date(2025, 10, 2), "Casey Demo", "Riverbend Lab", "Lab test", 85, 40, 32, 8, False),
    (date(2025, 11, 11), "Casey Demo", "Maple Family Medicine", "Preventive visit", 260, 180, 180, 0, True),
    (date(2025, 12, 4), "Alex Demo", "Harbor Dermatology", "Office visit", 350, 210, 160, 50, True),
]

# fill date, member (first name), drug, amount billed, plan paid, patient responsibility
PHARMACY = (
    [(date(2025, m, 3), "Alex", "Demostatin 10 mg", 41, 14, 10) for m in range(1, 13)]
    + [(date(2025, m, 10), "Casey", "Exampleair inhaler", 260, 150, 35) for m in range(1, 12, 2)]
    + [(date(2025, 7, 16), "Drew", "Amoxidemo 250 mg", 22, 5, 10)]
)

# service date, member (first name), billed, contracted (= covered), plan paid, patient responsibility
DENTAL = (
    [(date(2025, m, d), name, 260, 175, 175, 0)
     for m, d in ((2, 6), (8, 7)) for name in ("Alex", "Blair", "Casey", "Drew")]
    + [(date(2025, 9, 18), "Casey", 320, 210, 168, 42)]
)


def usd(v: float) -> str:
    return f"${v:,.2f}"


def mdy(d: date) -> str:
    return d.strftime("%m/%d/%Y")


def long_date(d: date) -> str:
    return f"{d:%B} {d.day}, {d.year}"


def write_csv(path: Path, header: list[str], rows: list[list[str]]):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def write_eob(path: Path, n: int, claim: tuple):
    """A fictional EOB in the simplified Cigna-like layout the extractor's tests use."""
    when, patient, provider, service, billed, allowed, paid, owes, _ = claim
    doc = pymupdf.open()
    cover = doc.new_page(width=612, height=792)
    cover.insert_text((65, 80), STAMP, fontsize=12)
    cover.insert_text((65, 100), "Fictional family, providers and amounts, made for the HSA vs. PPO demo.", fontsize=9)
    cover.insert_text((65, 263), "Explanation of payment. The payment is for the claim below and is not a bill.")
    cover.insert_text((65, 290), ADDRESS, fontsize=9)
    page = doc.new_page(width=612, height=792)
    page.insert_text((71, 60), STAMP, fontsize=9)
    page.insert_text((71, 140), f"Patient: {patient}", fontsize=9)
    page.insert_text((71, 153), f"Member ID: {MEMBER_ID}", fontsize=9)
    page.insert_text((71, 166), f"Provider: {provider}", fontsize=9)
    page.insert_text((71, 179), f"Service: {service}", fontsize=9)
    page.insert_text((71, 192), f"Claim # {CLAIM_PREFIX}{n:06d}", fontsize=9)
    page.insert_text((405, 210), "Service date", fontsize=9)
    page.insert_text((405, 223), long_date(when), fontsize=9)
    page.insert_text((405, 271), "Provider Network Status:", fontsize=9)
    page.insert_text((405, 281), "IN NETWORK", fontsize=9)
    page.insert_text((71, 438), f"Summary of a claim for services on {long_date(when)}", fontsize=9)
    page.insert_text((71, 497), "Amount Billed", fontsize=9)
    page.insert_text((175, 497), usd(billed), fontsize=11)
    page.insert_text((71, 521), "Cost Reduction", fontsize=9)
    page.insert_text((175, 521), usd(billed - allowed), fontsize=11)
    page.insert_text((71, 578), "What your plan", fontsize=9)
    page.insert_text((71, 591), "paid", fontsize=9)
    page.insert_text((180, 585), usd(paid), fontsize=11)
    page.insert_text((71, 639), "What I owe", fontsize=9)
    page.insert_text((180, 639), usd(owes), fontsize=11)
    doc.set_metadata({"title": STAMP})
    doc.save(path)
    doc.close()


def write_inputs(folder: Path) -> dict[str, Path]:
    """Write every synthetic input into `folder` and return their paths."""
    folder.mkdir(parents=True, exist_ok=True)
    eob_dir = folder / "eob_medical"
    eob_dir.mkdir(exist_ok=True)
    paths = {
        "medical": folder / "Claim_Summary_medical.csv",
        "dental": folder / "Claim_Summary_dental.csv",
        "pharmacy": folder / "Claim_Summary_pharmacy.csv",
        "eob_dir": eob_dir,
        "leak_terms": folder / "leak_terms.txt",
    }
    write_csv(paths["medical"], ["Service Date", "Patient", "Provider", "Status", "Billed", "Plan Paid",
                                 "Patient Responsibility", "I Owe", "My Payments"],
              [[mdy(d), who, prov, "Processed", usd(b), usd(p), usd(o), usd(0), usd(o)]
               for d, who, prov, _svc, b, _a, p, o, _pdf in MEDICAL])
    write_csv(paths["dental"], ["Service Date", "Patient", "Provider", "Status", "Billed", "Not Allowed",
                                "Covered Amount", "Contracted Amount", "Remaining Balance", "Plan Paid",
                                "Not Covered By Plan", "Applied To Deductible", "Patient Responsibility",
                                "My Payments"],
              [[mdy(d), who, "Bright Smile Dental", "Processed", usd(b), usd(0), usd(c), usd(c), usd(o), usd(p),
                usd(0), usd(0), usd(o), usd(o)] for d, who, b, c, p, o in DENTAL])
    write_csv(paths["pharmacy"], ["Fill Date", "Rx Number", "Drug Name", "Pharmacy Name", "Prescribed By", "For",
                                  "Amount Billed", "My Cigna Plan Paid", "Patient Responsibility"],
              [[mdy(d), f"RX-{n:04d}", drug, "Corner Pharmacy", "Dr. Example", who, usd(b), usd(p), usd(o)]
               for n, (d, who, drug, b, p, o) in enumerate(PHARMACY, start=1)])
    for n, claim in enumerate(MEDICAL, start=1):
        if claim[-1]:
            write_eob(eob_dir / f"EOB_{claim[0]:%Y-%m-%d}_{claim[1].split()[0]}_{n:02d}.pdf", n, claim)
    # Extra private terms for the leak scan, as a real user would list their address and member ID.
    paths["leak_terms"].write_text("\n".join([MEMBER_ID, ADDRESS, "Sampleton", "Bright Smile Dental",
                                              "Corner Pharmacy", "Dr. Example"]) + "\n", encoding="utf-8")
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description="Write the demo's synthetic claim CSVs, EOB PDFs and leak terms.")
    ap.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "output" / "inputs")
    args = ap.parse_args()
    paths = write_inputs(args.output)
    pdfs = len(list(paths["eob_dir"].glob("*.pdf")))
    print(f"Synthetic inputs -> {args.output}: {len(MEDICAL)} medical, {len(PHARMACY)} pharmacy, "
          f"{len(DENTAL)} dental claim rows; {pdfs} EOB PDFs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
