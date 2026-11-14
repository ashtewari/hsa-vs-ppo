"""Shared, standard-library table reading for the data-intake tools (CSV, or XLSX when openpyxl is installed)."""
from __future__ import annotations

import csv
import re
from datetime import date, datetime
from pathlib import Path

MONEY_RE = re.compile(r"^\$?\s*\(?\s*-?\s*\$?\s*-?\s*(?:\d[\d,]*)?\.?\d+\s*-?\s*\)?$")
MONTH_FIRST = ("%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y", "%m.%d.%Y")
DAY_FIRST = ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y", "%d.%m.%Y")
UNAMBIGUOUS = ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%d-%b-%Y", "%d %b %Y", "%b %d, %Y", "%B %d, %Y",
               "%b %d %Y", "%B %d %Y")


def read_table(path: Path, sheet: str | None = None, skip_rows: int = 0) -> tuple[list[str], list[list[str]]]:
    """Return (header, rows) with every cell as a stripped string. Values stay in memory."""
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise ValueError("Reading .xlsx needs openpyxl (pip install openpyxl), or save the sheet as CSV.") from exc
        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb[sheet] if sheet else wb.worksheets[0]
        raw = [[_cell(v) for v in row] for row in ws.iter_rows(values_only=True)]
        wb.close()
    else:
        text = None
        for enc in ("utf-8-sig", "cp1252"):
            try:
                text = path.read_text(encoding=enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError(f"Cannot decode {path.name} as UTF-8 or Windows-1252.")
        raw = [[c.strip() for c in row] for row in csv.reader(text.splitlines())]
    raw = raw[skip_rows:]
    while raw and not any(raw[0]):
        raw.pop(0)
    if not raw:
        raise ValueError(f"{path.name}: no header row found.")
    header = [h.strip() for h in raw[0]]
    rows = [r + [""] * (len(header) - len(r)) for r in raw[1:] if any(c for c in r)]
    return header, [r[:len(header)] for r in rows]


def sheet_names(path: Path) -> list[str]:
    if path.suffix.lower() not in (".xlsx", ".xlsm"):
        return []
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True)
    names = wb.sheetnames
    wb.close()
    return names


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else f"{v:.6f}".rstrip("0").rstrip(".")
    return str(v).strip()


def is_money(s: str) -> bool:
    return bool(MONEY_RE.match(s.strip())) if s.strip() else False


def date_formats(values: list[str], explicit: str | None = None) -> tuple[str, ...]:
    """Pick the formats for a column: month-first unless some value only parses day-first."""
    if explicit:
        return (explicit,)
    needs_day_first = any(_parse(v, UNAMBIGUOUS + MONTH_FIRST) is None and _parse(v, DAY_FIRST) is not None
                          for v in values if v)
    return UNAMBIGUOUS + (DAY_FIRST if needs_day_first else MONTH_FIRST)


def is_ambiguous_date_column(values: list[str]) -> bool:
    """True if every value reads both month-first and day-first (e.g. 03/04/2025) and they disagree somewhere."""
    vals = [v for v in values if v and _parse(v, UNAMBIGUOUS) is None]
    if not vals:
        return False
    both = [(_parse(v, MONTH_FIRST), _parse(v, DAY_FIRST)) for v in vals]
    return all(a and b for a, b in both) and any(a != b for a, b in both)


def parse_date(value: str, formats: tuple[str, ...]) -> datetime | None:
    return _parse(value, formats)


def _parse(value: str, formats: tuple[str, ...]) -> datetime | None:
    value = value.strip()
    for f in formats:
        try:
            return datetime.strptime(value, f)
        except ValueError:
            continue
    return None
