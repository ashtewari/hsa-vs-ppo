#!/usr/bin/env python3
"""Describe an unfamiliar claims file WITHOUT printing its values.

For each column: header name, filled cells, detected kind (money / date / number / text), distinct count, date range
as YYYY-MM, and hints such as "looks like an identifier" or "may contain names". An AI assistant can use this to
plan a column mapping without ever seeing personal data.

  python tools/profile_table.py FILE [--sheet NAME] [--skip-rows N] [--show-values COLUMN]

--show-values prints the distinct values of ONE column, only if it has at most 25 distinct values and no digits.
Use it for categorical columns such as claim type, status or network, never for names or providers.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from intake_table import date_formats, is_ambiguous_date_column, is_money, parse_date, read_table, sheet_names  # noqa: E402

NAME_HINT = re.compile(r"patient|member|name|for\b|subscriber|insured|dependent|person", re.I)
PROVIDER_HINT = re.compile(r"provider|doctor|physician|facility|pharmacy|prescrib|clinic|hospital", re.I)
ID_HINT = re.compile(r"\bid\b|number|#|claim|reference|account|rx|policy|group", re.I)


def classify(values: list[str]) -> str:
    filled = [v for v in values if v]
    if not filled:
        return "empty"
    if all(is_money(v) for v in filled):
        return "money" if any("." in v or "$" in v for v in filled) else "number"
    fmts = date_formats(filled)
    if all(parse_date(v, fmts) for v in filled):
        return "date (ambiguous M/D vs D/M)" if is_ambiguous_date_column(filled) else "date"
    return "text"


def profile(path: Path, sheet: str | None, skip_rows: int) -> list[dict]:
    header, rows = read_table(path, sheet, skip_rows)
    out = []
    for i, name in enumerate(header):
        values = [r[i] for r in rows]
        filled = [v for v in values if v]
        kind = classify(values)
        info = {"index": i + 1, "name": name or f"(blank header #{i + 1})", "filled": len(filled), "rows": len(rows),
                "kind": kind, "distinct": len(set(filled)), "hints": []}
        if kind.startswith("date"):
            fmts = date_formats(filled)
            months = sorted(d.strftime("%Y-%m") for d in (parse_date(v, fmts) for v in filled) if d)
            info["range"] = f"{months[0]}..{months[-1]}" if months else ""
        if kind == "money":
            info["hints"].append(f"{sum(1 for v in filled if v.startswith('-') or v.startswith('(') or v.endswith('-'))} negative")
        if kind in ("text", "number") and filled and len(set(filled)) >= 0.8 * len(filled) and any(re.search(r"\d{5,}", v) for v in filled):
            info["hints"].append("looks like an identifier (never output)")
        if kind == "text" and NAME_HINT.search(name):
            info["hints"].append("may contain personal names")
        if kind == "text" and PROVIDER_HINT.search(name):
            info["hints"].append("may contain provider / pharmacy / drug names")
        if (ID_HINT.search(name) and "looks like an identifier (never output)" not in info["hints"]
                and len(set(filled)) > 0.5 * max(len(filled), 1)):
            info["hints"].append("header suggests an identifier")
        out.append(info)
    return out


def safe_label(text: str, what: str, n: int) -> str:
    """Show a header or sheet name only if it looks like a label, not data (a title row may hold a name or ID)."""
    if len(text) <= 40 and not re.search(r"\d{4,}", text) and len(text.split()) <= 6:
        return repr(text)
    return f"(hidden {what} #{n}: looks like data rather than a label; check --skip-rows)"


def main() -> int:
    ap = argparse.ArgumentParser(description="Describe a CSV/XLSX file's columns without printing its values.")
    ap.add_argument("file", type=Path)
    ap.add_argument("--sheet", help="Worksheet name for .xlsx (default: the first sheet)")
    ap.add_argument("--skip-rows", type=int, default=0, help="Rows above the header row to skip")
    ap.add_argument("--show-values", metavar="COLUMN", help="Print distinct values of one categorical column")
    args = ap.parse_args()
    try:
        sheets = sheet_names(args.file)
        cols = profile(args.file, args.sheet, args.skip_rows)
        header, rows = read_table(args.file, args.sheet, args.skip_rows)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"File type: {args.file.suffix.lower() or '(none)'}"
          + (f"; sheets: {', '.join(safe_label(s, 'sheet', i) for i, s in enumerate(sheets, 1))}" if sheets else ""))
    print(f"Data rows: {len(rows)}; columns: {len(cols)}")
    if len(cols) <= 2 or sum(1 for c in cols if c["name"].startswith("(blank")) > len(cols) / 2:
        print("WARNING: the header row looks wrong. Try --skip-rows N (title or summary rows above the table).")
    for c in cols:
        extra = f" range {c['range']}" if c.get("range") else ""
        hints = f"  [{'; '.join(c['hints'])}]" if c["hints"] else ""
        print(f"{c['index']:3d}. {safe_label(c['name'], 'header', c['index'])}: {c['kind']}, filled {c['filled']}/{c['rows']}, distinct {c['distinct']}{extra}{hints}")
    if args.show_values:
        if args.show_values not in header:
            print(f"Error: no column named {args.show_values!r}", file=sys.stderr)
            return 1
        values = sorted({r[header.index(args.show_values)] for r in rows if r[header.index(args.show_values)]})
        if NAME_HINT.search(args.show_values) or PROVIDER_HINT.search(args.show_values) or re.search(r"drug|medic|diagnos|descr", args.show_values, re.I):
            print("Refused: this column may hold names, providers, drugs or descriptions.")
            return 1
        if len(values) > 25 or any(re.search(r"\d", v) or len(v) > 30 or len(v.split()) > 4 for v in values):
            print("Refused: --show-values is only for short categorical values (<= 25 distinct, no digits).")
            return 1
        print(f"Distinct values of {args.show_values!r}: {values}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
