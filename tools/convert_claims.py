#!/usr/bin/env python3
"""Convert any insurer's claims export (CSV/XLSX) into the claim-summary layouts the extractor reads.

The extractor recognises three layouts by header (medical, dental, pharmacy; see cigna_eob_local/README.md).
This tool maps your file's columns onto one of them, normalises dates and money, and prints counts only.

  python tools/convert_claims.py --kind medical --input FILE --output .local/claims_medical.csv \
      --map "Service Date=Date of Service" --map "Patient=Member" --map "Billed=Charges" \
      --map "Plan Paid=Paid by Plan" --map "Patient Responsibility=You Owe" --set "Status=Processed"

  python tools/convert_claims.py --kind medical --template --output .local/manual_medical.csv   # blank layout

Options: --where "Claim Type=Medical" (keep rows whose column equals a value; repeat for OR within one column,
AND across columns), --sheet, --skip-rows, --date-format (e.g. %d/%m/%Y), --patient-ids-to-names (when the
patient column holds member IDs rather than names), --force (overwrite the output).
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from intake_table import date_formats, is_ambiguous_date_column, parse_date, read_table  # noqa: E402

LAYOUTS = {
    "medical": ["Service Date", "Patient", "Provider", "Status", "Billed", "Plan Paid", "Patient Responsibility"],
    "dental": ["Service Date", "Patient", "Provider", "Status", "Billed", "Not Allowed", "Covered Amount",
               "Contracted Amount", "Plan Paid", "Not Covered By Plan", "Patient Responsibility"],
    "pharmacy": ["Fill Date", "Rx Number", "Drug Name", "For", "Amount Billed", "My Cigna Plan Paid",
                 "Patient Responsibility"],
}
OPTIONAL = {"Provider", "Rx Number", "Drug Name"}
DATE_COLUMNS = {"Service Date", "Fill Date"}
MONEY_COLUMNS = {"Billed", "Plan Paid", "Patient Responsibility", "Not Allowed", "Covered Amount",
                 "Contracted Amount", "Not Covered By Plan", "Amount Billed", "My Cigna Plan Paid"}


def parse_money(s: str) -> Decimal | None:
    t = s.strip().replace("$", "").replace(",", "").replace(" ", "")
    if not t:
        return None
    negative = t.startswith("-") or t.endswith("-") or (t.startswith("(") and t.endswith(")"))
    t = t.strip("()-")
    try:
        v = Decimal(t).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    return -v if negative else v


def pairs(items: list[str], flag: str) -> list[tuple[str, str]]:
    out = []
    for item in items or []:
        if "=" not in item:
            raise ValueError(f"{flag} expects NAME=VALUE")
        k, v = item.split("=", 1)
        out.append((k.strip(), v.strip()))
    return out


def ids_to_names(value: str) -> str:
    """Turn a member ID into a letters-only token so each person still gets their own anonymous tag."""
    return "".join("abcdefghij"[int(ch)] if ch.isdigit() else ch for ch in value if ch.isalnum()).capitalize()


def convert(args) -> dict:
    layout = LAYOUTS[args.kind]
    if args.output.exists() and not args.force:
        raise ValueError(f"{args.output.name} already exists; use --force to overwrite.")
    if args.template:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(layout)
        return {"template": True}
    mapping = dict(pairs(args.map, "--map"))
    constants = dict(pairs(args.set, "--set"))
    unknown = [k for k in list(mapping) + list(constants) if k not in layout]
    if unknown:
        raise ValueError(f"Not a {args.kind} layout column: {', '.join(unknown)}. Columns: {', '.join(layout)}")
    missing = [c for c in layout if c not in mapping and c not in constants and c not in OPTIONAL]
    if missing:
        raise ValueError(f"Map (--map) or set (--set) these columns: {', '.join(missing)}")
    header, rows = read_table(args.input, args.sheet, args.skip_rows)
    absent = [src for src in mapping.values() if src not in header]
    wheres = defaultdict(set)
    for col, val in pairs(args.where, "--where"):
        wheres[col].add(val.casefold())
    absent += [c for c in wheres if c not in header]
    if absent:
        raise ValueError(f"Columns not found in the input header: {', '.join(absent)}. Run profile_table.py to list them.")
    idx = {canon: header.index(src) for canon, src in mapping.items()}
    kept = [r for r in rows if all(r[header.index(c)].casefold() in vals for c, vals in wheres.items())]

    date_col = "Service Date" if "Service Date" in layout else "Fill Date"
    raw_dates = [r[idx[date_col]] for r in kept] if date_col in idx else []
    if not args.date_format and is_ambiguous_date_column(raw_dates):
        raise ValueError("Dates read both as month/day and day/month. Pass --date-format, e.g. %m/%d/%Y or %d/%m/%Y.")
    fmts = date_formats(raw_dates, args.date_format)

    skipped: Counter = Counter()
    skipped_note: Counter = Counter()
    out_rows = []
    months = []
    patients = set()
    for r in kept:
        rec = {}
        bad = None
        for col in layout:
            value = constants.get(col, r[idx[col]] if col in idx else "")
            if col in DATE_COLUMNS:
                d = parse_date(value, fmts)
                if d is None:
                    bad = "unreadable or missing date"
                    break
                months.append(d.strftime("%Y-%m"))
                value = d.strftime("%m/%d/%Y")
            elif col in MONEY_COLUMNS:
                if not value.strip() and col not in ("Billed", "Amount Billed"):
                    skipped_note[f"blank {col} read as $0.00"] += 1
                    value = "0"
                m = parse_money(value)
                if m is None:
                    bad = f"unreadable or missing {col}"
                    break
                value = f"{m:.2f}"
            elif col in ("Patient", "For") and args.patient_ids_to_names:
                value = ids_to_names(value)
            rec[col] = value
        if bad:
            skipped[bad] += 1
            continue
        patients.add(rec.get("Patient", rec.get("For", "")).casefold())
        out_rows.append(rec)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=layout)
        w.writeheader()
        w.writerows(out_rows)
    first_names = {(p.replace(",", " ").split() or [""])[0] for p in patients if p}
    return {"read": len(rows), "kept": len(kept), "written": len(out_rows), "skipped": dict(skipped),
            "notes": dict(skipped_note), "range": f"{min(months)}..{max(months)}" if months else "",
            "patients": len(patients - {""}), "first_names": len(first_names)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Map a claims export onto the extractor's claim-summary layout.")
    ap.add_argument("--kind", choices=sorted(LAYOUTS), required=True)
    ap.add_argument("--input", type=Path)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--map", action="append", default=[], metavar="LAYOUT_COLUMN=INPUT_COLUMN")
    ap.add_argument("--set", action="append", default=[], metavar="LAYOUT_COLUMN=VALUE")
    ap.add_argument("--where", action="append", default=[], metavar="INPUT_COLUMN=VALUE")
    ap.add_argument("--sheet")
    ap.add_argument("--skip-rows", type=int, default=0)
    ap.add_argument("--date-format")
    ap.add_argument("--patient-ids-to-names", action="store_true")
    ap.add_argument("--template", action="store_true", help="Write an empty layout for manual entry")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if not args.template and args.input is None:
        ap.error("--input is required unless --template")
    try:
        result = convert(args)
    except (OSError, ValueError, KeyError, csv.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if result.get("template"):
        print(f"Wrote an empty {args.kind} layout -> {args.output}")
        return 0
    print(f"Rows read {result['read']}, kept by --where {result['kept']}, written {result['written']}"
          f" ({args.kind}); service months {result['range']}; distinct patients {result['patients']}")
    for reason, n in sorted(result["skipped"].items()):
        print(f"  skipped {n}: {reason}")
    for note, n in sorted(result["notes"].items()):
        print(f"  note {n}: {note}")
    if result["first_names"] < result["patients"]:
        print("WARNING: different patient values share a first word, so the extractor would give them the same "
              "member tag. Use --patient-ids-to-names on an ID column, or fix the names locally.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
