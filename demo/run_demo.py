#!/usr/bin/env python3
"""One-command demo: a fictional family's year of claims -> de-identified CSVs -> HSA vs. PPO workbook.

Runs the same steps a real user runs (README "Quick start"), on synthetic data only:
  1. write synthetic myCigna claim CSVs and EOB PDFs (demo/make_demo_data.py)
  2. extract       -> review_candidates.csv (two claims have no EOB and are flagged for review)
  3. review        -> approve the two flagged rows (a real user checks them in myCigna first)
  4. finalize      -> claims_anonymized.csv
  5. leak scan     -> no names, providers, drugs, IDs or dates in the outputs
  6. validate      -> the fictional example plan parameters
  7. cost model    -> hsa_comparison_demo.xlsx

Usage:  python demo/run_demo.py [--output demo/output]
Everything is written under --output, which is wiped and recreated on every run (only if a previous demo run
created it, or it is empty).
"""
from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "demo"))
sys.path.insert(0, str(ROOT / "hsa_cost_model"))
import hsa_cost_model as cm  # noqa: E402
import make_demo_data  # noqa: E402

PLANS = ROOT / "plan_parameters" / "plan_parameters.example.json"
WORKBOOK_NAME = "hsa_comparison_demo.xlsx"
MARKER = ".hsa_demo_output"  # only folders carrying this marker are ever wiped


def prepare_output(out: Path):
    """Recreate the output folder, refusing to wipe anything the demo did not create (e.g. .local)."""
    if out.exists():
        if not (out / MARKER).is_file() and any(out.iterdir()):
            raise SystemExit(f"{out} is not empty and was not created by the demo; choose another --output folder.")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / MARKER).write_text("Created by demo/run_demo.py; wiped and recreated on every run.\n", encoding="utf-8")


def show(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def step(title: str):
    print(f"\n=== {title} ===", flush=True)


def run(*args: Path | str):
    cmd = [sys.executable, *map(str, args)]
    print("> python " + " ".join(show(Path(a)) if isinstance(a, Path) else a for a in args), flush=True)
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit(f"Demo step failed (exit code {result.returncode}).")


def approve_flagged_rows(review: Path) -> int:
    """Stand-in for the manual review: approve rows whose only issue is a missing EOB PDF."""
    with review.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields, rows = reader.fieldnames, list(reader)
    approved = 0
    for row in rows:
        if row["approved"] != "yes" and row["issue_codes"] == "no_matching_eob_pdf":
            row["approved"], row["allowed_basis"] = "yes", "manually_verified"
            approved += 1
    with review.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return approved


def headline(claims_csv: Path) -> dict:
    """Typical-year net cost per plan and the baseline check, as printed at the end of the demo."""
    params = cm.load_params(PLANS)
    claims = cm.load_claims_files([claims_csv], int(params.get("plan_year_start_month", 1)))
    cfg = cm.Config()
    typical = {r["plan"]: r["net"] for r in cm.scenarios(claims, params, cfg) if r["scenario"] == "typical"}
    return {"year": cm.base_year(claims, cfg), "names": {p: params["plans"][p]["name"] for p in typical},
            "net": typical, "current": cm.current_plan_id(params),
            "baseline": cm.baseline_check(claims, params, cfg)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the HSA vs. PPO toolkit end to end on a fictional family.")
    ap.add_argument("--output", type=Path, default=ROOT / "demo" / "output",
                    help="Folder for all demo files (wiped and recreated; must be empty or a previous demo output)")
    args = ap.parse_args(argv)
    out = args.output.resolve()
    prepare_output(out)
    inputs, extracted, workbook = out / "inputs", out / "extract", out / WORKBOOK_NAME

    step("1. Write synthetic inputs (fictional family; nothing real)")
    paths = make_demo_data.write_inputs(inputs)
    print(f"{len(make_demo_data.MEDICAL)} medical, {len(make_demo_data.PHARMACY)} pharmacy and "
          f"{len(make_demo_data.DENTAL)} dental claims for 2025; "
          f"{len(list(paths['eob_dir'].glob('*.pdf')))} EOB PDFs -> {show(inputs)}")
    claims_csvs = [paths["medical"], paths["dental"], paths["pharmacy"]]

    step("2. Extract de-identified claims")
    run(Path("cigna_eob_local/cigna_eob_extract.py"), "extract", "--quiet", "--claims-csv", *claims_csvs,
        "--input", paths["eob_dir"], "--output", extracted)

    step("3. Review flagged rows")
    review = extracted / "review_candidates.csv"
    n = approve_flagged_rows(review)
    print(f"Approved {n} rows flagged no_matching_eob_pdf. With real data you would check each one in myCigna "
          f"(or Excel) before setting approved=yes.")

    step("4. Finalize")
    run(Path("cigna_eob_local/cigna_eob_extract.py"), "finalize", "--review", review, "--output", extracted)

    step("5. Leak scan of the outputs against the private inputs")
    run(Path("cigna_eob_local/leak_scan.py"), "--outputs", extracted, "--claims-csv", *claims_csvs,
        "--input", paths["eob_dir"], "--terms-file", paths["leak_terms"])

    step("6. Validate the (fictional) plan parameters")
    run(Path("plan_parameters/validate_plan_parameters.py"), PLANS)

    step("7. Compare plans")
    claims_csv = extracted / "claims_anonymized.csv"
    run(Path("hsa_cost_model/hsa_cost_model.py"), "--claims", claims_csv, "--plans", PLANS, "--output", workbook)

    h = headline(claims_csv)
    current = h["current"]
    print(f"\n=== Result for the fictional family, plan year {h['year']} (estimate) ===")
    for pid, net in sorted(h["net"].items(), key=lambda kv: kv[1]):
        diff = net - h["net"][current]
        note = "" if pid == current else f"  ({'saves' if diff < 0 else 'costs'} ${abs(diff):,.0f} vs. {h['names'][current]})"
        print(f"  {h['names'][pid]:16s} net ${net:>9,.0f}{note}")
    print(f"\nOpen {show(workbook)} in Excel or LibreOffice. Start on 'Annual comparison', then try the yellow "
          f"cells on 'Parameters'.")
    print("These are fictional claims and plans. To run it on your own data, see README.md 'Quick start'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
