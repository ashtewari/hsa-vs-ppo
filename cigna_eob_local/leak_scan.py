#!/usr/bin/env python3
"""Local-only PHI leak scan for extractor outputs (PRD section 12.2, item 4).

Collects sensitive values from the private inputs (claim CSV names, providers, drugs, Rx numbers,
exact dates, PDF filenames and claim numbers, plus an optional terms file) and searches every CSV
in the output folder for them. Prints counts per class only, never the values themselves.
Exit code 0 = no matches, 1 = possible leak (or error).
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cigna_eob_extract as eob  # noqa: E402

TOKEN_RE = re.compile(r"[^\W_]+")
SENSITIVE_COLUMNS = {
    "names": ("Patient", "For"),
    "providers": ("Provider", "Pharmacy Name", "Prescribed By"),
    "drugs": ("Drug Name",),
    "rx_numbers": ("Rx Number",),
    "exact_dates": ("Service Date", "Fill Date"),
}


def safe_vocabulary() -> set[str]:
    """Words the extractor itself may legitimately write (column names, categories, codes)."""
    words = set()
    for group in (eob.VALID_CATEGORIES, eob.COVERAGE_TYPES, eob.SOURCES, eob.ALLOWED_BASES, eob.NETWORKS,
                  eob.FIELDS, eob.ACCEPTED_FIELDS, eob.SUMMARY_FIELDS, eob.CATEGORY_FIELDS, eob.ISSUE_CODES):
        for item in group:
            words.update(t.casefold() for t in TOKEN_RE.findall(item))
    return words


def collect_secrets(claims_csvs: list[Path], pdf_dir: Path | None, terms_file: Path | None) -> dict[str, set[str]]:
    secrets: dict[str, set[str]] = {k: set() for k in (*SENSITIVE_COLUMNS, "filenames", "claim_numbers", "pdf_ids", "terms")}
    for path in claims_csvs:
        secrets["filenames"].add(path.stem)
        for row in eob.read_dict_rows(path):
            for cls, cols in SENSITIVE_COLUMNS.items():
                for col in cols:
                    if row.get(col):
                        secrets[cls].add(row[col])
                        if cls == "exact_dates":
                            d = eob.parse_date(row[col])
                            if d:
                                secrets[cls].add(d.strftime("%Y-%m-%d"))
    if pdf_dir is not None:
        for pdf in sorted(p for p in pdf_dir.rglob("*") if p.suffix.lower() == ".pdf"):
            secrets["filenames"].add(pdf.stem)
            parsed = eob.parse_pdf(pdf)
            if parsed["claim_ref"]:
                secrets["claim_numbers"].add(parsed["claim_ref"])
            if parsed["date"]:
                secrets["exact_dates"].add(parsed["date"].strftime("%Y-%m-%d"))
                secrets["exact_dates"].add(parsed["date"].strftime("%m/%d/%Y"))
            try:
                with eob.fitz.open(pdf) as doc:
                    text = " ".join(page.get_text() for page in doc)
                # Member, account, group and claim identifiers printed on the EOB.
                secrets["pdf_ids"].update(re.findall(r"\b[A-Z]{0,2}\d{7,}\b", text))
            except Exception:
                pass
    if terms_file is not None:
        secrets["terms"].update(t.strip() for t in terms_file.read_text(encoding="utf-8-sig").splitlines() if t.strip())
    return secrets


def scan(output_dir: Path, secrets: dict[str, set[str]]) -> dict[str, tuple[int, int]]:
    """Return {class: (values checked, values found)}. Values are matched whole and by word."""
    text = "\n".join(p.read_text(encoding="utf-8-sig") for p in sorted(output_dir.glob("*.csv"))).casefold()
    out_tokens = set(TOKEN_RE.findall(text))
    vocab = safe_vocabulary()
    results = {}
    for cls, values in secrets.items():
        found = 0
        for value in values:
            v = value.casefold()
            tokens = {t for t in TOKEN_RE.findall(v) if len(t) >= 3 and not t.isdigit() and t not in vocab}
            if cls in ("rx_numbers", "claim_numbers", "pdf_ids"):
                hit = v in out_tokens
            elif TOKEN_RE.fullmatch(v):
                hit = bool(tokens & out_tokens)
            else:
                hit = bool(re.search(r"(?<![^\W_])" + re.escape(v) + r"(?![^\W_])", text)) or bool(tokens & out_tokens)
            found += hit
        results[cls] = (len(values), found)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Search extractor outputs for sensitive values from the private inputs.")
    parser.add_argument("--outputs", type=Path, required=True, help="Folder containing the extractor's CSV outputs")
    parser.add_argument("--claims-csv", type=Path, nargs="*", default=[], help="The private myCigna claim CSVs used as input")
    parser.add_argument("--input", type=Path, help="The private EOB PDF folder used as input")
    parser.add_argument("--terms-file", type=Path, help="Optional local text file of extra terms (member ID, address, ...), one per line")
    args = parser.parse_args()
    try:
        results = scan(args.outputs, collect_secrets(args.claims_csv, args.input, args.terms_file))
    except (OSError, csv.Error, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    leaks = 0
    for cls, (checked, found) in results.items():
        print(f"{cls:14s} checked {checked:4d}  found in outputs: {found}")
        leaks += found
    print("PASS: no sensitive input values found in output CSVs." if not leaks
          else "FAIL: possible leak. Inspect the outputs locally; do not share them.")
    return 0 if not leaks else 1


if __name__ == "__main__":
    raise SystemExit(main())
