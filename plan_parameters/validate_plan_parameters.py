#!/usr/bin/env python3
"""Validate a plan-parameters JSON file for the Phase 2 HSA vs. PPO comparison.

Standard library only. Prints a summary, the values still missing (null), recorded assumptions,
and IRS-rule checks for HSA-eligible plans. Exit code 0 = valid (missing values allowed), 1 = errors.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

TIERS = ("employee", "employee_spouse", "employee_children", "family")
FAMILY_TYPES = ("embedded", "aggregate")
RULES = {
    "covered_100": (),
    "copay": ("copay",),
    "coinsurance_no_deductible": (),
    "deductible_then_coinsurance": (),
    "deductible_then_covered_100": (),
}
SERVICES = ("preventive", "office_visit_pcp", "office_visit_specialist", "complex_imaging", "xray_lab",
            "inpatient", "outpatient_hospital", "urgent_care", "emergency")
RX_TIERS = ("retail_30", "retail_90", "mail_90")


class Report:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.missing: list[str] = []

    def number(self, obj: dict, key: str, path: str, lo: float = 0, hi: float | None = None) -> float | None:
        if key not in obj:
            self.errors.append(f"{path}.{key}: required")
            return None
        v = obj[key]
        if v is None:
            self.missing.append(f"{path}.{key}")
            return None
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < lo or (hi is not None and v > hi):
            self.errors.append(f"{path}.{key}: must be a number in [{lo}, {hi if hi is not None else 'inf'}]")
            return None
        return float(v)

    def section(self, obj: Any, key: str, path: str) -> dict:
        v = obj.get(key) if isinstance(obj, dict) else None
        if not isinstance(v, dict):
            self.errors.append(f"{path}.{key}: required object")
            return {}
        return v


def check_limits(r: Report, plan: dict, path: str) -> dict[str, Any]:
    ded = r.section(plan, "deductible", path)
    oop = r.section(plan, "oop_max", path)
    vals: dict[str, Any] = {
        "ded_ind": r.number(ded, "individual", f"{path}.deductible"),
        "ded_fam": r.number(ded, "family", f"{path}.deductible"),
        "oop_ind": r.number(oop, "individual", f"{path}.oop_max"),
        "oop_fam": r.number(oop, "family", f"{path}.oop_max"),
        "coins": r.number(plan, "coinsurance", path, 0, 1),
    }
    for name, sec in (("deductible", ded), ("oop_max", oop)):
        if sec.get("family_type") not in FAMILY_TYPES:
            r.errors.append(f"{path}.{name}.family_type: must be one of {FAMILY_TYPES}")
    vals["ded_type"] = ded.get("family_type")
    vals["per_person_cap"] = r.number(ded, "per_person_cap", f"{path}.deductible") if ded.get("per_person_cap") is not None else None
    for a, b in (("ded_ind", "oop_ind"), ("ded_fam", "oop_fam")):
        if vals[a] is not None and vals[b] is not None and vals[a] > vals[b]:
            r.errors.append(f"{path}: deductible exceeds out-of-pocket maximum ({a} > {b})")
    return vals


def check_hsa_rules(r: Report, path: str, inn: dict, irs: dict, family_cov: bool, employer_amount: float | None):
    checks = (("ded_ind", "hdhp_min_deductible_self", "min"), ("ded_fam", "hdhp_min_deductible_family", "min"),
              ("oop_ind", "hdhp_max_oop_self", "max"), ("oop_fam", "hdhp_max_oop_family", "max"))
    for key, limit, kind in checks:
        v, lim = inn.get(key), irs.get(limit)
        if v is not None and lim is not None and ((kind == "min" and v < lim) or (kind == "max" and v > lim)):
            r.errors.append(f"{path}.in_network: {key} {v:g} violates IRS {limit} {lim:g}")
    if family_cov and inn.get("ded_type") == "embedded":
        person = inn.get("per_person_cap") or inn.get("ded_ind")
        lim = irs.get("hdhp_min_deductible_family")
        if person is not None and lim is not None and person < lim:
            r.errors.append(f"{path}.in_network: embedded per-person deductible {person:g} is below the IRS family "
                            f"minimum {lim:g}; family coverage would not be HSA-eligible")
    cap = irs.get("hsa_contribution_family" if family_cov else "hsa_contribution_self")
    if employer_amount is not None and cap is not None and employer_amount > cap:
        r.errors.append(f"{path}.employer_hsa exceeds the IRS contribution limit")


def check_services(r: Report, inn_section: dict, path: str):
    services = r.section(inn_section, "services", path)
    for svc in SERVICES:
        rule = services.get(svc)
        if rule is None:
            r.missing.append(f"{path}.services.{svc}")
        elif not isinstance(rule, dict) or rule.get("rule") not in RULES:
            r.errors.append(f"{path}.services.{svc}.rule: must be one of {tuple(RULES)}")
        else:
            for needed in RULES[rule["rule"]]:
                r.number(rule, needed, f"{path}.services.{svc}")
    rx = r.section(inn_section, "pharmacy", path)
    if not isinstance(rx.get("deductible_applies"), bool):
        r.errors.append(f"{path}.pharmacy.deductible_applies: must be true/false")
    for t in RX_TIERS:
        copays = rx.get(t)
        if copays is None:
            r.missing.append(f"{path}.pharmacy.{t}")
        elif not (isinstance(copays, list) and len(copays) == 3
                  and all(isinstance(c, (int, float)) and not isinstance(c, bool) and c >= 0 for c in copays)):
            r.errors.append(f"{path}.pharmacy.{t}: must be [tier1, tier2, tier3] copays")


def validate(data: dict) -> Report:
    r = Report()
    if data.get("schema_version") != 1:
        r.errors.append("schema_version: must be 1")
    r.number(data, "plan_year", "", 2000, 2100)
    r.number(data, "plan_year_start_month", "", 1, 12)
    r.number(data, "pay_periods_per_year", "", 1, 52)
    tier = data.get("coverage_tier")
    if tier not in TIERS:
        r.errors.append(f"coverage_tier: must be one of {TIERS}")
    family_cov = tier in ("employee_spouse", "employee_children", "family")
    sources = data.get("sources") if isinstance(data.get("sources"), dict) else {}

    irs_section = r.section(data, "irs_limits", "")
    irs = {k: r.number(irs_section, k, "irs_limits") for k in (
        "hsa_contribution_self", "hsa_contribution_family", "hsa_catch_up_55", "hdhp_min_deductible_self",
        "hdhp_min_deductible_family", "hdhp_max_oop_self", "hdhp_max_oop_family")}
    tax = r.section(data, "tax", "")
    for key in ("federal_marginal_rate", "state_marginal_rate", "fica_rate"):
        r.number(tax, key, "tax", 0, 1)
    for key in ("hsa_via_payroll", "age_55_plus"):
        if key not in tax:
            r.errors.append(f"tax.{key}: required")
        elif tax[key] is None:
            r.missing.append(f"tax.{key}")
        elif not isinstance(tax[key], bool):
            r.errors.append(f"tax.{key}: must be true/false")
    fsa = r.number(tax, "healthcare_fsa_annual", "tax")

    plans = r.section(data, "plans", "")
    current = data.get("current_plan")
    if current is not None and current not in plans:
        r.errors.append("current_plan: must be the id of one of the plans")
    elif current is None and sum(1 for p in plans.values() if isinstance(p, dict) and not p.get("hsa_eligible")) != 1:
        r.errors.append("current_plan: required unless exactly one plan is non-HSA (it names the plan that produced "
                        "the claims history)")
    for pid, plan in plans.items():
        path = f"plans.{pid}"
        if plan.get("source") not in sources:
            r.errors.append(f"{path}.source: must reference a key in 'sources'")
        prem = r.section(plan, "premium_per_paycheck", path)
        for t in TIERS:
            r.number(prem, t, f"{path}.premium_per_paycheck")
        er = r.section(plan, "employer_hsa", path)
        er_self = r.number(er, "self", f"{path}.employer_hsa")
        er_other = r.number(er, "other_tiers", f"{path}.employer_hsa")
        inn_section = r.section(plan, "in_network", path)
        inn = check_limits(r, inn_section, f"{path}.in_network")
        check_limits(r, r.section(plan, "out_of_network", path), f"{path}.out_of_network")
        check_services(r, inn_section, f"{path}.in_network")
        if plan.get("hsa_eligible") is True:
            if fsa:
                r.warnings.append(f"{path}: a general-purpose healthcare FSA disqualifies HSA contributions")
            check_hsa_rules(r, path, inn, irs, family_cov, er_other if family_cov else er_self)
        elif (er_self or 0) or (er_other or 0):
            r.warnings.append(f"{path}: employer HSA money on a plan that is not HSA-eligible")

    assumptions = data.get("assumptions", [])
    if not isinstance(assumptions, list) or not all(isinstance(a, dict) and "path" in a and "note" in a
                                                    for a in assumptions):
        r.errors.append("assumptions: must be a list of {path, note}")
    return r


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a plan-parameters JSON file.")
    parser.add_argument("file", type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    r = validate(data)
    tier = data.get("coverage_tier")
    periods = data.get("pay_periods_per_year") or 0
    print(f"Plan year {data.get('plan_year')}, tier {tier}, {periods} pay periods")
    for pid, plan in (data.get("plans") or {}).items():
        prem = (plan.get("premium_per_paycheck") or {}).get(tier)
        annual = f"${prem * periods:,.2f}/yr" if isinstance(prem, (int, float)) else "premium missing"
        print(f"  {pid:10s} {plan.get('name', '')}: {annual}")
    for a in data.get("assumptions", []) if isinstance(data.get("assumptions"), list) else []:
        print(f"ASSUMPTION {a.get('path')}: {a.get('note')}")
    for m in r.missing:
        print(f"MISSING {m}")
    for w in r.warnings:
        print(f"WARNING {w}")
    for e in r.errors:
        print(f"ERROR {e}")
    print("INVALID" if r.errors else f"VALID ({len(r.missing)} missing value(s))")
    return 1 if r.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
