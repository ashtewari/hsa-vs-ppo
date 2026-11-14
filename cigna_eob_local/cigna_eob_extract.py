#!/usr/bin/env python3
"""Offline, conservative Cigna claims-to-de-identified-CSV extractor.

Never sends data to a network. Reads local text-searchable EOB PDFs via PyMuPDF and,
optionally, myCigna "Claim Summary" CSV exports and a user-maintained estimates file.
Creates a review queue; refuses to silently include doubtful claims.

This is a financial extraction aid, not a definitive interpretation of benefits.
See README.md for security considerations and review instructions.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

try:
    import pymupdf as fitz  # reads local PDFs only
except ImportError:
    sys.exit("Install the offline PDF reader: python -m pip install pymupdf")

MONEY_RE = re.compile(r"(?<![A-Za-z0-9])\$?\(?-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}\)?(?!\d)")
DATE_RE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2},?\s+20\d{2}\b|\b(?:0?[1-9]|1[0-2])[/.-](?:0?[1-9]|[12]\d|3[01])[/.-](?:20\d{2}|\d{2})\b",
    re.I,
)
SUMMARY_RE = re.compile(r"\bsummary of (?:a )?claim for services on\b", re.I)
# Summary labels must start their (possibly wrapped) line, so explanatory sentences such as
# "Your plan paid $x" or "...not covered amount..." are never read as labels.
LABELS = {
    "billed": [r"amount billed\b", r"charges? submitted\b", r"total billed\b"],
    "discount": [r"discount\b", r"network savings\b", r"cost reduction\b"],
    "not_covered": [r"amount not covered\b", r"not covered amount\b"],
    "allowed": [r"covered amount\b", r"allowed amount\b", r"amount allowed\b", r"negotiated amount\b"],
    "plan_paid": [r"what (?:cigna|your) plan paid\b", r"what (?:cigna|insurance) paid\b", r"plan paid\b"],
    "member_owes": [r"what (?:i|you) owe\b", r"you may owe\b", r"patient responsibility\b"],
}
PATTERNS = {key: [re.compile(r"^\W*" + x, re.I) for x in group] for key, group in LABELS.items()}
VALID_CATEGORIES = {
    "medical_unspecified", "office_visit", "preventive", "laboratory", "imaging",
    "urgent_care", "emergency", "inpatient", "outpatient_procedure", "therapy", "pharmacy",
    "dental", "vision", "other",
}
COVERAGE_TYPES = ("medical", "dental", "pharmacy", "vision")
SOURCES = ("eob_pdf", "claims_csv", "claims_csv+eob_pdf", "estimate")
ALLOWED_BASES = (
    "direct_eob", "derived_from_billed_discount_notcovered", "derived_from_paid_plus_owed",
    "direct_claims_csv", "estimated", "manually_verified",
)
NETWORKS = ("unknown", "in_network", "out_of_network")
ROW_ID_RE = re.compile(r"(?:EOB|CLM|EST)_\d{5,}")
MEMBER_TAG_RE = re.compile(r"Member_[A-Za-z0-9]{1,12}")
MONTH_RE = re.compile(r"20\d{2}-(?:0[1-9]|1[0-2])")
FIELDS = [
    "approved", "eob_id", "service_month", "coverage_type", "category", "member_tag", "network",
    "billed", "discount", "not_covered", "allowed_estimated", "allowed_basis",
    "plan_paid", "member_owes", "source", "review_status", "issue_codes",
]
ACCEPTED_FIELDS = ["eob_id", "service_month", "sequence_in_month", "coverage_type", "category", "member_tag",
                   "network", "billed", "discount", "not_covered", "allowed_estimated", "allowed_basis",
                   "plan_paid", "member_owes", "source", "review_status"]
SUMMARY_FIELDS = ["year", "coverage_type", "data_basis", "accepted_claims", "total_billed", "estimated_allowed",
                  "insurance_paid", "member_responsibility", "not_covered"]
CATEGORY_FIELDS = ["year", "coverage_type", "category", "data_basis", "accepted_claims", "estimated_allowed",
                   "member_responsibility"]
ESTIMATE_FIELDS = ["member_tag", "coverage_type", "category", "start_month", "end_month", "every_n_months",
                   "unit_allowed", "unit_plan_paid", "unit_member_owes"]
AMOUNT_KEYS = ("billed", "discount", "not_covered", "allowed", "plan_paid", "member_owes")
# Every code the extractor can emit (documented in README "Issue codes").
ISSUE_CODES = (
    "password_protected", "empty_pdf", "scanned_or_no_selectable_text", "cannot_open_or_parse_pdf",
    "multiple_claim_summaries", *(f"ambiguous_{k}" for k in ("billed", "discount", "not_covered", "allowed",
                                                              "plan_paid", "member_owes")),
    "missing_service_date", *(f"missing_{k}" for k in AMOUNT_KEYS), "negative_or_adjusted_claim",
    "billed_discount_allowed_inconsistent", "paid_plus_owed_not_reconciled", "noncovered_not_in_member_owes",
    "noncovered_amount_needs_review", "duplicate_pdf", "possible_reprocessed_claim", "cannot_hash_pdf",
    "claim_denied", "claim_not_final", "possible_duplicate_claim_row", "no_matching_eob_pdf",
    "ambiguous_pdf_match", "pdf_csv_member_owes_mismatch", "multiple_pdfs_for_claim",
)
APPROVED_VALUES = {"yes", "y", "true", "1"}
CENT = Decimal("0.01")

# myCigna "Claim Summary" exports, recognised by header. Checked in order: dental is a superset of medical.
CLAIM_CSV_FORMATS = {
    "dental": {"Service Date", "Patient", "Status", "Billed", "Not Allowed", "Covered Amount",
               "Contracted Amount", "Plan Paid", "Not Covered By Plan", "Patient Responsibility"},
    "pharmacy": {"Fill Date", "For", "Amount Billed", "My Cigna Plan Paid", "Patient Responsibility"},
    "medical": {"Service Date", "Patient", "Status", "Billed", "Plan Paid", "Patient Responsibility"},
}


MONEY_VALUE_RE = re.compile(r"\$?\s*(\()?\s*(-)?\s*\$?\s*(-)?\s*((?:\d[\d,]*)?\.?\d+)\s*(-)?\s*(\))?")


def parse_money(s: str | None) -> Decimal | None:
    if not s or not str(s).strip():
        return None
    m = MONEY_VALUE_RE.fullmatch(str(s).strip())
    if not m or bool(m.group(1)) != bool(m.group(6)):
        return None
    negative = bool(m.group(1) or m.group(2) or m.group(3) or m.group(5))
    try:
        value = Decimal(m.group(4).replace(",", "")).quantize(CENT)
    except InvalidOperation:
        return None
    return -value if negative else value


def fmt_money(v: Decimal | None) -> str:
    return "" if v is None else f"{v:.2f}"


def unique_issues(issues: list[str]) -> str:
    return ";".join(dict.fromkeys(x for x in issues if x))


def add_issue(row: dict, code: str):
    row["approved"] = "no"
    row["issue_codes"] = unique_issues(row["issue_codes"].split(";") + [code])
    if row.get("review_status") != "estimate":
        row["review_status"] = "needs_manual_review"


def extract_lines(page: Any) -> list[dict]:
    """Keep coordinates only in memory; never write extracted PHI or raw text to disk."""
    result = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            text = " ".join(x.get("text", "").strip() for x in spans if x.get("text", "").strip())
            if text:
                b = line["bbox"]
                result.append({"text": text, "x0": b[0], "y0": b[1], "x1": b[2], "y1": b[3]})
    return sorted(result, key=lambda r: (round(r["y0"] / 3), r["x0"]))


def _label_like(line: dict) -> bool:
    return len(line["text"]) <= 30 and not re.search(r"\d", line["text"])


def merge_wrapped_labels(lines: list[dict]) -> list[dict]:
    """Join labels that wrap onto a second line in the same column (e.g. 'Amount not' / 'covered')."""
    used: set[int] = set()
    merged = []
    for i, line in enumerate(lines):
        if i in used:
            continue
        if _label_like(line):
            for j, other in enumerate(lines):
                if (j != i and j not in used and _label_like(other) and abs(other["x0"] - line["x0"]) <= 2
                        and -4 <= other["y0"] - line["y1"] <= 6):
                    line = {"text": f"{line['text']} {other['text']}", "x0": min(line["x0"], other["x0"]),
                            "y0": line["y0"], "x1": max(line["x1"], other["x1"]), "y1": other["y1"]}
                    used.add(j)
                    break
        merged.append(line)
    return sorted(merged, key=lambda r: (round(r["y0"] / 3), r["x0"]))


def find_nearby_amount(label_line: dict, match_end: int, lines: list[dict]) -> Decimal | None:
    """Match amount beside a summary label; fall back to immediately adjacent line."""
    tail = label_line["text"][match_end:]
    tokens = MONEY_RE.findall(tail)
    if tokens:
        return parse_money(tokens[0])

    # A separate text span often contains the amount on the SAME horizontal row.
    choices = []
    for line in lines:
        if line is label_line:
            continue
        amounts = MONEY_RE.findall(line["text"])
        if len(amounts) != 1:
            continue
        center_diff = abs((line["y0"] + line["y1"]) / 2 - (label_line["y0"] + label_line["y1"]) / 2)
        if center_diff <= 6 and line["x0"] >= label_line["x1"] - 3:
            choices.append((0, center_diff, line["x0"] - label_line["x1"], amounts[0]))
        elif 0 <= line["y0"] - label_line["y1"] <= 15:
            # Only permit next-line values if that line is essentially just the value.
            if len(line["text"].strip()) <= 25 and not re.search(r"[A-Za-z]", line["text"]):
                choices.append((1, line["y0"] - label_line["y1"], abs(line["x0"] - label_line["x0"]), amounts[0]))
    if not choices:
        return None
    choices.sort(key=lambda x: x[:3])
    return parse_money(choices[0][3])


def label_column(lines: list[dict]) -> float | None:
    """x position of the summary label column, taken from the 'Amount billed' label when unambiguous."""
    xs = {round(l["x0"]) for l in lines for r in PATTERNS["billed"] if r.search(l["text"])}
    return xs.pop() if len(xs) == 1 else None


def extract_summary_values(lines: list[dict]) -> tuple[dict, list[str]]:
    values: dict[str, Decimal | None] = {}
    issues: list[str] = []
    column = label_column(lines)
    if column is not None:
        # Wrapped explanatory sentences elsewhere on the page can start with label words.
        lines = [l for l in lines if abs(l["x0"] - column) <= 5 or MONEY_RE.fullmatch(l["text"].strip())]
    for key, variants in PATTERNS.items():
        found: list[Decimal] = []
        for line in lines:
            for regex in variants:
                match = regex.search(line["text"])
                if match:
                    value = find_nearby_amount(line, match.end(), lines)
                    if value is not None:
                        found.append(value)
                    break
        if found:
            values[key] = found[0]
            if len(set(found)) > 1:
                issues.append(f"ambiguous_{key}")
        else:
            values[key] = None
    # Cigna omits the 'Amount not covered' row when it is zero. Infer $0 only when the label column
    # has no 'not covered' text at all and every other summary amount was found.
    if (column is not None and values["not_covered"] is None
            and all(values[k] is not None for k in ("billed", "discount", "plan_paid", "member_owes"))
            and not any(re.search(r"\bnot\s+covered\b", l["text"], re.I) for l in lines)):
        values["not_covered"] = Decimal("0.00")
    return values, issues


def find_service_date(lines: list[dict]) -> datetime | None:
    # Search only lines explicitly describing service dates, not claim received/processed dates.
    lines_text = [line["text"] for line in lines]
    candidates = []
    for i, item in enumerate(lines_text):
        if re.search(r"summary of (?:a )?claim for services on|date(?:s)? of service|service date(?:s)?", item, re.I):
            source = item + " " + " ".join(lines_text[i + 1:i + 3])
            candidates.extend(DATE_RE.findall(source[:190]))
    for token in candidates:
        parsed = parse_date(token)
        if parsed:
            return parsed
    return None


def parse_date(token: str) -> datetime | None:
    for f in ("%B %d, %Y", "%B %d %Y", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y", "%m.%d.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(token.strip(), f)
        except ValueError:
            continue
    return None


def find_network(lines: list[dict]) -> str:
    """Cigna prints 'Provider Network Status: IN NETWORK' (value may sit on the next line)."""
    for line in lines:
        if re.search(r"provider network status", line["text"], re.I):
            below = [o["text"] for o in lines if abs(o["x0"] - line["x0"]) <= 3 and -4 <= o["y0"] - line["y1"] <= 15]
            text = " ".join([line["text"]] + below)
            if re.search(r"\bout[- ]of[- ]network\b", text, re.I):
                return "out_of_network"
            if re.search(r"\bin[- ]network\b", text, re.I):
                return "in_network"
    return "unknown"


def broad_category(lines: list[dict]) -> str:
    # Local-only heuristic, output is an allowed categorical token, NEVER raw description.
    text = " ".join(x["text"] for x in lines).lower()
    # Restrict to fairly unambiguous phrases, avoid diagnosing/coding conditions.
    options = [
        (r"\bpharmacy claim\b|\bprescription drug\b", "pharmacy"),
        (r"\bemergency room\b|\bemergency department\b", "emergency"),
        (r"\burgent care\b", "urgent_care"),
        (r"\binpatient hospitalization\b|\binpatient hospital\b", "inpatient"),
        (r"\bpreventive visit\b|\bannual physical\b", "preventive"),
        (r"\bx-?ray\b|\bmri\b|\bct scan\b|\bdiagnostic imaging\b|\bradiology\b", "imaging"),
        (r"\blaboratory services\b|\bblood test\b|\blab test\b", "laboratory"),
        (r"\bphysical therapy\b|\boccupational therapy\b", "therapy"),
        (r"\boutpatient surgery\b|\boutpatient procedure\b", "outpatient_procedure"),
        (r"\boffice visit\b|\bphysician office visit\b", "office_visit"),
    ]
    matches = [cat for patt, cat in options if re.search(patt, text)]
    return matches[0] if len(matches) == 1 else "medical_unspecified"


def find_claim_reference(text: str) -> str | None:
    """Internal-only reference used to flag possible duplicate/reprocessed claims."""
    match = re.search(r"(?:reference\s*(?:#|number)|claim\s*(?:#|number))\s*(?:id)?\s*:?\s*([0-9][0-9\s-]{7,25})",
                      text, re.I)
    return re.sub(r"\D", "", match.group(1)) if match else None


def parse_pdf(path: Path) -> dict:
    """Parse one EOB in memory. The returned exact date and claim reference must never be written out."""
    parsed: dict[str, Any] = {"values": {x: None for x in LABELS}, "date": None, "category": "medical_unspecified",
                              "network": "unknown", "issues": [], "claim_ref": None}
    issues = parsed["issues"]
    try:
        with fitz.open(path) as doc:
            if doc.needs_pass:
                issues.append("password_protected")
            elif len(doc) == 0:
                issues.append("empty_pdf")
            else:
                page_texts = [p.get_text() for p in doc]
                summary_pages = [i for i, t in enumerate(page_texts) if SUMMARY_RE.search(t)]
                page_no = summary_pages[0] if summary_pages else 0
                lines = extract_lines(doc[page_no])
                if len("".join(l["text"] for l in lines)) < 80:
                    issues.append("scanned_or_no_selectable_text")
                else:
                    if sum(len(SUMMARY_RE.findall(t)) for t in page_texts) > 1:
                        issues.append("multiple_claim_summaries")
                    lines = merge_wrapped_labels(lines)
                    parsed["values"], value_issues = extract_summary_values(lines)
                    issues += value_issues
                    parsed["date"] = find_service_date(lines)
                    if not parsed["date"]:
                        issues.append("missing_service_date")
                    parsed["category"] = broad_category(lines)
                    parsed["network"] = find_network(lines)
                    parsed["claim_ref"] = find_claim_reference(page_texts[page_no])
    except Exception:
        # Deliberately do not include exception messages, filenames or PDF text in output.
        issues.append("cannot_open_or_parse_pdf")
    return parsed


def new_record(row_id: str, coverage_type: str, source: str) -> dict:
    rec: dict[str, Any] = {k: None for k in AMOUNT_KEYS}
    rec.update({"id": row_id, "date": None, "month": "", "coverage_type": coverage_type, "category": "medical_unspecified",
                "member_tag": "", "network": "unknown", "basis": "", "source": source, "issues": []})
    return rec


def apply_pdf_values(rec: dict, parsed: dict):
    v = parsed["values"]
    for key in ("discount", "not_covered", "allowed"):
        rec[key] = v[key]
    rec["basis"] = "direct_eob" if v["allowed"] is not None else ""
    if rec["allowed"] is None and all(v[k] is not None for k in ("billed", "discount", "not_covered")):
        rec["allowed"] = v["billed"] - v["discount"] - v["not_covered"]
        rec["basis"] = "derived_from_billed_discount_notcovered"
    rec["category"] = parsed["category"]
    rec["network"] = parsed["network"]
    rec["issues"] += parsed["issues"]


def build_row(rec: dict) -> dict[str, str]:
    """Apply the shared conservative checks; a row is auto-approved only if no issue is raised."""
    issues = list(rec["issues"])
    billed, discount, not_covered, allowed, plan_paid, member_owes = (rec[k] for k in AMOUNT_KEYS)
    for required, v in (("billed", billed), ("plan_paid", plan_paid), ("member_owes", member_owes), ("allowed", allowed)):
        if v is None:
            issues.append(f"missing_{required}")
    if not_covered is None:
        issues.append("missing_not_covered")
    if discount is None:
        issues.append("missing_discount")
    if any(v is not None and v < 0 for v in (billed, discount, not_covered, allowed, plan_paid, member_owes)):
        issues.append("negative_or_adjusted_claim")
    if all(v is not None for v in (billed, discount, not_covered, allowed)):
        if abs(billed - discount - not_covered - allowed) > CENT:
            issues.append("billed_discount_allowed_inconsistent")
    if all(v is not None for v in (allowed, plan_paid, member_owes, not_covered)):
        # For normal in-network claims, patient share + insurer payment should
        # equal allowed + any uncovered patient liability. If not, review manually.
        if abs(plan_paid + member_owes - allowed - not_covered) > CENT:
            if not_covered != 0 and abs(plan_paid + member_owes - allowed) <= CENT:
                # Cigna's 'What I owe' excludes the not-covered amount, which you may or may not owe.
                issues.append("noncovered_not_in_member_owes")
            else:
                issues.append("paid_plus_owed_not_reconciled")
    if not_covered is not None and not_covered != 0:
        issues.append("noncovered_amount_needs_review")

    approved = "yes" if not issues else "no"
    if rec["source"] == "estimate":
        status = "estimate"
    else:
        status = "auto_validated" if approved == "yes" else "needs_manual_review"
    month = rec["month"] or (rec["date"].strftime("%Y-%m") if rec["date"] else "")
    return {
        "approved": approved,
        "eob_id": rec["id"],
        "service_month": month,
        "coverage_type": rec["coverage_type"],
        "category": rec["category"],
        "member_tag": rec["member_tag"],
        "network": rec["network"],
        "billed": fmt_money(billed),
        "discount": fmt_money(discount),
        "not_covered": fmt_money(not_covered),
        "allowed_estimated": fmt_money(allowed),
        "allowed_basis": rec["basis"],
        "plan_paid": fmt_money(plan_paid),
        "member_owes": fmt_money(member_owes),
        "source": rec["source"],
        "review_status": status,
        "issue_codes": unique_issues(issues),
    }


def review_one(path: Path, eob_id: str) -> dict[str, str]:
    parsed = parse_pdf(path)
    rec = new_record(eob_id, "medical", "eob_pdf")
    rec["date"] = parsed["date"]
    rec["billed"] = parsed["values"]["billed"]
    rec["plan_paid"] = parsed["values"]["plan_paid"]
    rec["member_owes"] = parsed["values"]["member_owes"]
    apply_pdf_values(rec, parsed)
    return build_row(rec)


# --- myCigna claim-summary CSV import (EX-28) -------------------------------------------------

def member_key(name: str | None) -> str:
    """First given name, case-folded. Medical exports use 'First Last'; dental/pharmacy use 'First'."""
    s = (name or "").strip()
    if "," in s:
        s = s.split(",", 1)[1]
    tokens = re.findall(r"[^\W\d_]+", s)
    return tokens[0].casefold() if tokens else ""


def member_tags(keys: set[str]) -> dict[str, str]:
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    ordered = sorted(k for k in keys if k)
    return {k: f"Member_{letters[i]}" if i < len(letters) else f"Member_{i + 1}" for i, k in enumerate(ordered)}


def read_dict_rows(path: Path) -> list[dict[str, str]]:
    """Read a CSV with stripped keys/values. Empty trailing extra fields are ignored; non-empty ones are an error."""
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        rows = []
        for n, r in enumerate(csv.DictReader(f), start=2):
            extra = r.pop(None, None) or []
            if any((x or "").strip() for x in extra):
                raise ValueError(f"{path.name} line {n}: more values than header columns")
            rows.append({(k or "").strip(): (v or "").strip() for k, v in r.items()})
        return rows


def read_claims_csv(path: Path) -> tuple[str, list[dict]]:
    rows = read_dict_rows(path)
    if not rows:
        raise ValueError(f"Claims CSV has no rows: {path.name}")
    header = set(rows[0])
    for kind, required in CLAIM_CSV_FORMATS.items():
        if required <= header:
            return kind, rows
    raise ValueError(f"Unrecognised claims CSV header (expected a myCigna Claim Summary export): {path.name}")


PROVIDER_CATEGORY_RULES = [
    (r"\blabcorp\b|\bquest diag|\blaborator|\blabs?\b|\bpatholog", "laboratory"),
    (r"\bradiolog|\bimaging\b|\bmri\b|\bx-?ray\b", "imaging"),
    (r"\burgent\b|\bminuteclinic\b|\bpatient first\b|\bwalk-?in\b", "urgent_care"),
    (r"\bemergency\b", "emergency"),
    (r"\bphysical therapy\b|\brehab", "therapy"),
]


def provider_category(provider: str) -> str:
    """Category from the provider's name (kept in memory); only the category token is ever output."""
    matches = {cat for patt, cat in PROVIDER_CATEGORY_RULES if re.search(patt, provider or "", re.I)}
    return matches.pop() if len(matches) == 1 else "medical_unspecified"


def csv_record(kind: str, raw: dict, row_id: str) -> tuple[dict, str, tuple]:
    """Build an in-memory record. Returns (record, member key, duplicate key); PHI stays in memory."""
    rec = new_record(row_id, kind, "claims_csv")
    rec["date"] = parse_date(raw.get("Fill Date" if kind == "pharmacy" else "Service Date", ""))
    if rec["date"] is None:
        rec["issues"].append("missing_service_date")
    if kind != "pharmacy":
        status = raw.get("Status", "").casefold()
        if status == "denied":
            rec["issues"].append("claim_denied")
        elif status not in ("processed", "paid", "finalized"):
            rec["issues"].append("claim_not_final")
    if kind == "pharmacy":
        rec["billed"] = parse_money(raw.get("Amount Billed"))
        rec["plan_paid"] = parse_money(raw.get("My Cigna Plan Paid"))
        rec["category"] = "pharmacy"
    else:
        rec["billed"] = parse_money(raw.get("Billed"))
        rec["plan_paid"] = parse_money(raw.get("Plan Paid"))
    if kind == "medical":
        rec["category"] = provider_category(raw.get("Provider", ""))
    rec["member_owes"] = parse_money(raw.get("Patient Responsibility"))
    if kind == "dental":
        rec["category"] = "dental"
        contracted = parse_money(raw.get("Contracted Amount"))
        not_allowed = parse_money(raw.get("Not Allowed"))
        not_covered_by_plan = parse_money(raw.get("Not Covered By Plan"))
        rec["allowed"] = parse_money(raw.get("Covered Amount"))
        if rec["billed"] is not None and contracted is not None:
            rec["discount"] = rec["billed"] - contracted
        if not_allowed is not None and not_covered_by_plan is not None:
            rec["not_covered"] = not_allowed + not_covered_by_plan
        rec["basis"] = "direct_claims_csv"
    who = raw.get("For" if kind == "pharmacy" else "Patient")
    detail = (raw.get("Rx Number", ""), raw.get("Drug Name", "")) if kind == "pharmacy" else (raw.get("Provider", ""),)
    dup_key = (kind, member_key(who), rec["date"], *detail, rec["billed"], rec["plan_paid"], rec["member_owes"])
    return rec, member_key(who), dup_key


def derive_from_paid_plus_owed(rec: dict):
    """Fallback when no EOB detail exists: allowed = plan paid + member share, nothing 'not covered'."""
    if rec["plan_paid"] is not None and rec["member_owes"] is not None:
        rec["allowed"] = rec["plan_paid"] + rec["member_owes"]
        rec["not_covered"] = Decimal("0.00")
        if rec["billed"] is not None:
            rec["discount"] = rec["billed"] - rec["allowed"]
        rec["basis"] = "derived_from_paid_plus_owed"


def month_range(start: str, end: str, step: int) -> list[str]:
    y, m = int(start[:4]), int(start[5:])
    out = []
    while f"{y:04d}-{m:02d}" <= end:
        out.append(f"{y:04d}-{m:02d}")
        m += step
        y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    return out


def read_estimates(path: Path, first_id: int = 1) -> list[dict]:
    """Expand user-entered estimates (no claim record exists) into monthly records."""
    rows = read_dict_rows(path)
    if rows and not set(ESTIMATE_FIELDS) <= set(rows[0]):
        raise ValueError(f"Estimates CSV must have columns: {', '.join(ESTIMATE_FIELDS)}")
    records = []
    for n, row in enumerate(rows, start=2):
        def bad(reason: str):
            raise ValueError(f"Estimates CSV line {n}: {reason}")
        if row["member_tag"] and not MEMBER_TAG_RE.fullmatch(row["member_tag"]):
            bad("member_tag must be blank or Member_X")
        if row["coverage_type"] not in COVERAGE_TYPES:
            bad(f"coverage_type must be one of {', '.join(COVERAGE_TYPES)}")
        if row["category"] not in VALID_CATEGORIES:
            bad("category is not in the allowed list")
        if not (MONTH_RE.fullmatch(row["start_month"]) and MONTH_RE.fullmatch(row["end_month"])):
            bad("start_month/end_month must be YYYY-MM")
        if row["start_month"] > row["end_month"]:
            bad("start_month is after end_month")
        if not row["every_n_months"].isdigit() or not 1 <= int(row["every_n_months"]) <= 120:
            bad("every_n_months must be a whole number from 1 to 120")
        allowed, paid, owes = (parse_money(row[k]) for k in ("unit_allowed", "unit_plan_paid", "unit_member_owes"))
        if any(v is None or v < 0 for v in (allowed, paid, owes)):
            bad("unit amounts must be non-negative numbers")
        if abs(paid + owes - allowed) > CENT:
            bad("unit_plan_paid + unit_member_owes must equal unit_allowed")
        for month in month_range(row["start_month"], row["end_month"], int(row["every_n_months"])):
            rec = new_record(f"EST_{first_id + len(records):05d}", row["coverage_type"], "estimate")
            rec.update({"month": month, "category": row["category"], "member_tag": row["member_tag"],
                        "billed": allowed, "discount": Decimal("0.00"), "not_covered": Decimal("0.00"),
                        "allowed": allowed, "plan_paid": paid, "member_owes": owes, "basis": "estimated"})
            records.append(rec)
    return records


# --- output -------------------------------------------------------------------------------

def write_csv(path: Path, fields: list[str], rows: list[dict]):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def validate_review_row(row: dict) -> tuple[bool, str]:
    if str(row.get("approved", "")).strip().lower() not in APPROVED_VALUES:
        return False, "not_approved"
    if not ROW_ID_RE.fullmatch(row.get("eob_id", "")):
        return False, "invalid_eob_id"
    if not MONTH_RE.fullmatch(row.get("service_month", "")):
        return False, "invalid_service_month"
    if row.get("coverage_type", "") not in COVERAGE_TYPES:
        return False, "invalid_coverage_type"
    if row.get("category", "") not in VALID_CATEGORIES:
        return False, "invalid_category"
    if row.get("network", "") not in NETWORKS:
        return False, "invalid_network"
    if row.get("member_tag", "") and not MEMBER_TAG_RE.fullmatch(row["member_tag"]):
        return False, "invalid_member_tag"
    if row.get("source", "") not in SOURCES:
        return False, "invalid_source"
    nums = {k: parse_money(row.get(k, "")) for k in ("billed", "discount", "not_covered", "allowed_estimated", "plan_paid", "member_owes")}
    if any(v is None for v in nums.values()):
        return False, "missing_or_invalid_amount"
    if any(v < 0 for v in nums.values()):
        return False, "negative_amount_manual_adjustment_required"
    if abs(nums["billed"] - nums["discount"] - nums["not_covered"] - nums["allowed_estimated"]) > CENT:
        return False, "billed_minus_discounts_does_not_equal_allowed"
    if abs(nums["plan_paid"] + nums["member_owes"] - nums["allowed_estimated"] - nums["not_covered"]) > CENT:
        return False, "insurance_paid_plus_member_owed_does_not_reconcile"
    if row.get("allowed_basis", "") not in ALLOWED_BASES:
        return False, "invalid_allowed_basis"
    return True, ""


def finalize(review_path: Path, out_dir: Path) -> tuple[int, int]:
    with review_path.open("r", newline="", encoding="utf-8-sig") as f:
        reviewed = list(csv.DictReader(f))
    if not reviewed or not set(FIELDS).issubset(set(reviewed[0])):
        raise ValueError("Review CSV has missing columns or no claim rows. "
                         "Files from an older version need 'extract --force' to rebuild.")
    accepted = []
    rejected = []
    seen_ids = set()
    for row in reviewed:
        if row.get("eob_id") in seen_ids:
            rejected.append((row.get("eob_id", "?"), "duplicate_eob_id"))
            continue
        seen_ids.add(row.get("eob_id"))
        good, reason = validate_review_row(row)
        if good:
            row = row.copy()
            if row["source"] == "estimate":
                row["review_status"] = "estimate"
            elif row.get("review_status") == "auto_validated" and not row.get("issue_codes"):
                row["review_status"] = "auto_validated"
            else:
                row["review_status"] = "manually_approved"
            accepted.append(row)
        elif reason != "not_approved":
            rejected.append((row.get("eob_id", "?"), reason))

    accepted.sort(key=lambda r: (r["service_month"], r["eob_id"]))
    counter = Counter()
    for r in accepted:
        counter[r["service_month"]] += 1
        r["sequence_in_month"] = str(counter[r["service_month"]]).zfill(3)
    write_csv(out_dir / "claims_anonymized.csv", ACCEPTED_FIELDS, accepted)

    yearly = defaultdict(lambda: {"count": 0, "billed": Decimal(0), "allowed": Decimal(0), "plan_paid": Decimal(0), "member_owes": Decimal(0), "not_covered": Decimal(0)})
    by_category = defaultdict(lambda: {"count": 0, "allowed": Decimal(0), "member_owes": Decimal(0)})
    for r in accepted:
        y = r["service_month"][:4]
        basis = "estimate" if r["source"] == "estimate" else "actual"
        d = yearly[(y, r["coverage_type"], basis)]
        d["count"] += 1
        for source, target in (("billed", "billed"), ("allowed_estimated", "allowed"), ("plan_paid", "plan_paid"), ("member_owes", "member_owes"), ("not_covered", "not_covered")):
            d[target] += parse_money(r[source])
        c = by_category[(y, r["coverage_type"], r["category"], basis)]
        c["count"] += 1
        c["allowed"] += parse_money(r["allowed_estimated"])
        c["member_owes"] += parse_money(r["member_owes"])

    summary = [{"year": y, "coverage_type": cov, "data_basis": basis, "accepted_claims": d["count"],
                "total_billed": fmt_money(d["billed"]), "estimated_allowed": fmt_money(d["allowed"]),
                "insurance_paid": fmt_money(d["plan_paid"]), "member_responsibility": fmt_money(d["member_owes"]),
                "not_covered": fmt_money(d["not_covered"])}
               for (y, cov, basis), d in sorted(yearly.items())]
    write_csv(out_dir / "annual_summary.csv", SUMMARY_FIELDS, summary)
    cats = [{"year": y, "coverage_type": cov, "category": cat, "data_basis": basis, "accepted_claims": d["count"],
             "estimated_allowed": fmt_money(d["allowed"]), "member_responsibility": fmt_money(d["member_owes"])}
            for (y, cov, cat, basis), d in sorted(by_category.items())]
    write_csv(out_dir / "annual_by_category.csv", CATEGORY_FIELDS, cats)

    print(f"Accepted {len(accepted)} de-identified claim rows -> {out_dir / 'claims_anonymized.csv'}")
    print(f"Not approved: {sum(str(x.get('approved', '')).lower() not in APPROVED_VALUES for x in reviewed)}")
    if rejected:
        print(f"Rejected {len(rejected)} approved rows due to validation errors (only anonymous IDs shown):")
        for eob_id, reason in rejected:
            print(f"  {eob_id}: {reason}")
    print("Annual totals include ACCEPTED claims ONLY; do not treat partial totals as complete utilization.")
    return len(accepted), len(rejected)


def protect_review_file(review_file: Path, force: bool):
    """RV-8: never silently overwrite a review file that may hold manual edits."""
    if not review_file.exists():
        return
    if not force:
        raise ValueError(f"{review_file} already exists and may contain your manual edits. "
                         "Run 'finalize' to rebuild the reports, or re-run 'extract' with --force "
                         "(a timestamped backup is made first).")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = review_file.with_name(f"review_candidates.backup-{stamp}.csv")
    n = 1
    while backup.exists():
        n += 1
        backup = review_file.with_name(f"review_candidates.backup-{stamp}-{n}.csv")
    shutil.copy2(review_file, backup)
    print(f"Backed up existing review file -> {backup}")


def scan_pdfs(input_dir: Path, id_prefix: str = "EOB") -> list[tuple[str, Path, dict, list[str]]]:
    """Parse every PDF; returns (anon id, path, parsed, duplicate issues). Filenames go to the terminal only."""
    pdfs = sorted((p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"), key=lambda p: str(p).lower())
    if not pdfs:
        raise ValueError("No PDF files found in the input directory.")
    results = []
    by_digest: dict[str, str] = {}
    by_ref: dict[str, list[list[str]]] = defaultdict(list)
    for n, pdf in enumerate(pdfs, start=1):
        anon = f"{id_prefix}_{n:05d}"
        print(f"Reading {n}/{len(pdfs)}: {pdf.name}  [{anon}]")  # terminal only; never in exported CSVs
        parsed = parse_pdf(pdf)
        dup_issues: list[str] = []
        try:
            digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
            if digest in by_digest:
                dup_issues.append("duplicate_pdf")
            else:
                by_digest[digest] = anon
        except OSError:
            dup_issues.append("cannot_hash_pdf")
        if parsed["claim_ref"]:
            by_ref[parsed["claim_ref"]].append(dup_issues)
        results.append((anon, pdf, parsed, dup_issues))
    for same_ref in by_ref.values():
        if len(same_ref) > 1:
            for dup_issues in same_ref:
                dup_issues.append("possible_reprocessed_claim")
    return results


def records_from_pdfs(input_dir: Path) -> list[dict]:
    records = []
    for anon, _pdf, parsed, dup_issues in scan_pdfs(input_dir):
        rec = new_record(anon, "medical", "eob_pdf")
        rec["date"] = parsed["date"]
        for key in ("billed", "plan_paid", "member_owes"):
            rec[key] = parsed["values"][key]
        apply_pdf_values(rec, parsed)
        rec["issues"] += dup_issues
        records.append(rec)
    return records


def records_from_claims_csvs(csv_paths: list[Path], input_dir: Path | None) -> list[dict]:
    records: list[dict] = []
    keys_by_id: dict[str, str] = {}
    dup_groups: dict[tuple, list[dict]] = defaultdict(list)
    for i, path in enumerate(sorted(csv_paths, key=lambda p: str(p).lower()), start=1):
        kind, raw_rows = read_claims_csv(path)
        first = len(records) + 1
        for raw in raw_rows:
            rec, key, dup_key = csv_record(kind, raw, f"CLM_{len(records) + 1:05d}")
            keys_by_id[rec["id"]] = key
            dup_groups[dup_key].append(rec)
            records.append(rec)
        print(f"Claims CSV {i}/{len(csv_paths)}: {path.name} -> {kind}, {len(raw_rows)} rows "
              f"[CLM_{first:05d}..CLM_{len(records):05d}]")  # terminal only
    for group in dup_groups.values():
        if len(group) > 1:
            for rec in group:
                rec["issues"].append("possible_duplicate_claim_row")

    tags = member_tags(set(keys_by_id.values()))
    for rec in records:
        rec["member_tag"] = tags.get(keys_by_id[rec["id"]], "")
    print("Member tags (terminal only; never written to CSV):")
    for key, tag in sorted(tags.items(), key=lambda kv: kv[1]):
        print(f"  {tag} = {key.title()}")

    # Fill allowed / discount / not-covered for medical rows from matching EOB PDFs (in memory only).
    medical = [r for r in records if r["coverage_type"] == "medical"]
    index: dict[tuple, list[dict]] = defaultdict(list)
    for rec in medical:
        if rec["date"] is not None:
            index[(rec["date"].date(), rec["billed"], rec["plan_paid"])].append(rec)
    matched: dict[str, list[tuple[str, dict, list[str]]]] = defaultdict(list)
    unmatched = []
    if input_dir is not None:
        for anon, pdf, parsed, dup_issues in scan_pdfs(input_dir):
            v = parsed["values"]
            key = (parsed["date"].date() if parsed["date"] else None, v["billed"], v["plan_paid"])
            hits = index.get(key, []) if None not in key else []
            if len(hits) == 1:
                matched[hits[0]["id"]].append((anon, parsed, dup_issues))
                print(f"  {anon} -> {hits[0]['id']}")
            elif len(hits) > 1:
                for rec in hits:
                    rec["issues"].append("ambiguous_pdf_match")
                print(f"  {anon} -> ambiguous ({len(hits)} claim rows)")
            else:
                unmatched.append((anon, pdf.name))
        if unmatched:
            print(f"{len(unmatched)} PDF(s) did not match any medical claim row (not imported):")
            for anon, name in unmatched:
                print(f"  {anon}: {name}")
    for rec in medical:
        hits = matched.get(rec["id"], [])
        if not hits:
            derive_from_paid_plus_owed(rec)
            if "ambiguous_pdf_match" not in rec["issues"]:
                rec["issues"].append("no_matching_eob_pdf")
            continue
        _anon, parsed, dup_issues = hits[0]
        csv_owes = rec["member_owes"]
        csv_category = rec["category"]
        apply_pdf_values(rec, parsed)
        if rec["category"] == "medical_unspecified":
            rec["category"] = csv_category
        rec["issues"] += dup_issues
        rec["source"] = "claims_csv+eob_pdf"
        pdf_owes = parsed["values"]["member_owes"]
        if pdf_owes is not None and csv_owes is not None and abs(pdf_owes - csv_owes) > CENT:
            rec["issues"].append("pdf_csv_member_owes_mismatch")
        if len(hits) > 1:
            rec["issues"].append("multiple_pdfs_for_claim")
    for rec in records:
        if rec["coverage_type"] == "pharmacy":
            derive_from_paid_plus_owed(rec)
    return records


def extract(input_dir: Path | None, output_dir: Path, claims_csvs: list[Path] | None = None,
            estimates: Path | None = None, force: bool = False):
    review_file = output_dir / "review_candidates.csv"
    protect_review_file(review_file, force)
    if claims_csvs:
        records = records_from_claims_csvs(list(claims_csvs), input_dir)
    elif input_dir is not None:
        records = records_from_pdfs(input_dir)
    else:
        raise ValueError("Provide --input (EOB PDFs) and/or --claims-csv.")
    if estimates is not None:
        est = read_estimates(estimates)
        print(f"Estimates: {len(est)} rows [EST_00001..EST_{len(est):05d}]")
        records += est
    rows = [build_row(rec) for rec in records]

    write_csv(review_file, FIELDS, rows)
    print(f"\nReview queue -> {review_file}")
    good = sum(x["approved"] == "yes" for x in rows)
    print(f"Auto-validated: {good}/{len(rows)}; needs review: {len(rows)-good}")
    finalize(review_file, output_dir)
    print("\nIf you correct any row in review_candidates.csv, set approved=yes and run 'finalize'.")
    print("IMPORTANT: no raw PDF text, names, provider info, claim numbers or exact dates written to CSVs.")


def main():
    parser = argparse.ArgumentParser(description="Offline Cigna EOB PDF / claims CSV -> de-identified financial CSVs; strict review checks.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_extract = sub.add_parser("extract", help="Build the review CSV and summaries from local PDFs and/or claim CSVs.")
    p_extract.add_argument("--input", type=Path, help="Folder containing original Cigna EOB PDFs")
    p_extract.add_argument("--claims-csv", type=Path, nargs="+", help="myCigna Claim Summary CSV export(s): medical, dental, pharmacy")
    p_extract.add_argument("--estimates", type=Path, help="Optional estimates CSV for spending with no claim record")
    p_extract.add_argument("--output", type=Path, required=True, help="Folder for de-identified CSVs")
    p_extract.add_argument("--force", action="store_true", help="Overwrite an existing review_candidates.csv after backing it up")
    p_final = sub.add_parser("finalize", help="Rebuild approved financial summaries after editing review CSV.")
    p_final.add_argument("--review", type=Path, required=True, help="Edited review_candidates.csv")
    p_final.add_argument("--output", type=Path, required=True, help="Folder for de-identified CSVs")
    args = parser.parse_args()
    try:
        if args.command == "extract":
            if args.input is None and not args.claims_csv:
                parser.error("provide --input and/or --claims-csv")
            if args.input is not None and not args.input.is_dir():
                parser.error("--input must be an existing directory")
            for path in args.claims_csv or []:
                if not path.is_file():
                    parser.error("--claims-csv files must exist")
            if args.estimates is not None and not args.estimates.is_file():
                parser.error("--estimates file must exist")
            args.output.mkdir(parents=True, exist_ok=True)
            extract(args.input.resolve() if args.input else None, args.output.resolve(),
                    claims_csvs=[p.resolve() for p in args.claims_csv or []],
                    estimates=args.estimates.resolve() if args.estimates else None, force=args.force)
        else:
            args.output.mkdir(parents=True, exist_ok=True)
            finalize(args.review.resolve(), args.output.resolve())
    except (ValueError, OSError, csv.Error) as exc:
        # Errors from filesystem may include sensitive paths; display locally only.
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
