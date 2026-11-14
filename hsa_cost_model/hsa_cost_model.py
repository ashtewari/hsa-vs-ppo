#!/usr/bin/env python3
"""HSA vs. PPO cost comparison model (PRD Phase 2).

Replays de-identified claims (claims_anonymized.csv from the Phase 1 extractor) chronologically under each
plan in a plan-parameters JSON file, then layers premiums, employer HSA funding and tax effects, and writes an
Excel workbook. Parameter-driven figures in the workbook are live formulas; the claim-by-claim replay is
computed here and written as values (re-run this script after changing cost-sharing rules).

Offline: reads two local files and writes one workbook. Every output is an ESTIMATE.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

CENT = Decimal("0.01")
ZERO = Decimal("0")
FAMILY_TIERS = ("employee_spouse", "employee_children", "family")
REPLAYED = ("medical", "pharmacy")
# Extractor category -> plan service key. Unmatched medical claims are inferred from the current plan's copays.
SERVICE_BY_CATEGORY = {
    "preventive": "preventive", "office_visit": "office_visit_pcp", "laboratory": "xray_lab",
    "imaging": "complex_imaging", "urgent_care": "urgent_care", "emergency": "emergency", "inpatient": "inpatient",
    "outpatient_procedure": "outpatient_hospital", "therapy": "office_visit_specialist",
}
COPAY_SERVICES = ("office_visit_pcp", "office_visit_specialist", "urgent_care", "emergency")
RX_CHANNELS = ("retail_30", "retail_90", "mail_90")
NO_DEDUCTIBLE_SERVICE = "xray_lab"          # current plan charged plain coinsurance
DEDUCTIBLE_SERVICE = "complex_imaging"      # current plan applied the deductible (imaging / outpatient-type)
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def money(v: Any) -> Decimal:
    return Decimal(str(v)).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Claim:
    id: str
    year: int
    month: int
    seq: int
    coverage_type: str
    category: str
    member: str
    network: str
    allowed: Decimal
    plan_paid: Decimal
    member_owes: Decimal
    not_covered: Decimal
    source: str

    @property
    def is_estimate(self) -> bool:
        return self.source == "estimate"


@dataclass
class Config:
    employee_hsa: Decimal | None = None          # None -> the maximum allowed
    employer_hsa_timing: str = "per_paycheck"     # or "lump_sum_january"
    premiums_pre_tax: bool = True
    investment_return: float = 0.06
    medical_inflation: float = 0.05
    premium_inflation: float = 0.05
    projection_years: int = 5
    starting_hsa_balance: Decimal = ZERO
    high_event_allowed: Decimal = Decimal("20000")
    baseline_tolerance: float = 0.10
    breakeven_max: Decimal = Decimal("40000")
    breakeven_step: Decimal = Decimal("500")
    base_year: int | None = None


@dataclass
class Result:
    plan: str
    claim: Claim
    service: str
    rule: str
    cost: Decimal
    deductible_applied: Decimal
    replayed: bool


# --- loading ----------------------------------------------------------------------------------------

def load_claims(path: Path, plan_year_start_month: int = 1) -> list[Claim]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    claims = []
    for r in rows:
        y, m = int(r["service_month"][:4]), int(r["service_month"][5:7])
        plan_year = y if m >= plan_year_start_month else y - 1
        claims.append(Claim(
            id=r["eob_id"], year=plan_year, month=m, seq=int(r.get("sequence_in_month") or 0),
            coverage_type=r["coverage_type"], category=r["category"], member=r["member_tag"] or "Member_unknown",
            network=r["network"], allowed=money(r["allowed_estimated"]), plan_paid=money(r["plan_paid"]),
            member_owes=money(r["member_owes"]), not_covered=money(r["not_covered"]), source=r["source"]))
    return claims


def load_claims_files(paths: list[Path], plan_year_start_month: int = 1) -> list[Claim]:
    """Combine several claims_anonymized.csv files (e.g. a PDF-only run and a CSV run); IDs are kept unique."""
    claims, seen = [], set()
    for i, path in enumerate(paths, start=1):
        for c in load_claims(path, plan_year_start_month):
            if c.id in seen:
                c = replace(c, id=f"{c.id}.{i}")
            seen.add(c.id)
            claims.append(c)
    return claims


REQUIRED_SERVICES = ("preventive", "office_visit_pcp", "office_visit_specialist", "complex_imaging", "xray_lab",
                     "inpatient", "outpatient_hospital", "urgent_care", "emergency")


def missing_inputs(params: dict) -> list[str]:
    """Values the model needs as numbers/rules. The validator allows nulls; the model can't."""
    missing = []

    def need(obj: Any, path: str, *keys: str):
        for k in keys:
            if not isinstance(obj, dict) or obj.get(k) is None:
                missing.append(f"{path}.{k}")

    need(params, "", "coverage_tier", "pay_periods_per_year")
    need(params.get("tax"), "tax", "federal_marginal_rate", "state_marginal_rate", "fica_rate")
    need(params.get("irs_limits"), "irs_limits", "hsa_contribution_self", "hsa_contribution_family", "hsa_catch_up_55")
    family = params.get("coverage_tier") in FAMILY_TIERS
    for pid, plan in (params.get("plans") or {}).items():
        p = f"plans.{pid}"
        need(plan.get("premium_per_paycheck"), f"{p}.premium_per_paycheck", params.get("coverage_tier", "employee"))
        if plan.get("hsa_eligible"):
            need(plan.get("employer_hsa"), f"{p}.employer_hsa", "other_tiers" if family else "self")
        for net in ("in_network", "out_of_network"):
            section = plan.get(net) or {}
            need(section.get("deductible"), f"{p}.{net}.deductible", "individual", "family", "family_type")
            need(section.get("oop_max"), f"{p}.{net}.oop_max", "individual", "family", "family_type")
            need(section, f"{p}.{net}", "coinsurance")
        inn = plan.get("in_network") or {}
        need(inn.get("services"), f"{p}.in_network.services", *REQUIRED_SERVICES)
        need(inn.get("pharmacy"), f"{p}.in_network.pharmacy", "deductible_applies")
    return missing


def load_params(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def current_plan_id(params: dict) -> str:
    """The plan whose cost sharing produced the historical claims: 'current_plan', else the only non-HSA plan."""
    explicit = params.get("current_plan")
    if explicit:
        if explicit not in params["plans"]:
            raise ValueError(f"current_plan '{explicit}' is not one of the plans.")
        return explicit
    ids = [pid for pid, p in params["plans"].items() if not p.get("hsa_eligible")]
    if len(ids) != 1:
        raise ValueError("Set 'current_plan' in the plan parameters to the plan whose cost sharing produced the "
                         "claims history (required unless exactly one plan is non-HSA).")
    return ids[0]


def family_coverage(params: dict) -> bool:
    return params["coverage_tier"] in FAMILY_TIERS


def combined_tax_rate(params: dict) -> Decimal:
    t = params["tax"]
    rate = Decimal(str(t["federal_marginal_rate"])) + Decimal(str(t["state_marginal_rate"]))
    if t.get("hsa_via_payroll"):
        rate += Decimal(str(t["fica_rate"]))
    return rate


# --- classification ---------------------------------------------------------------------------------

def classify(claim: Claim, current: dict) -> tuple[str, Decimal]:
    """Return (service key, copay) for a claim. Copay is only meaningful for pharmacy."""
    if claim.coverage_type == "pharmacy":
        # Tier is unknown; the copay actually charged under the current plan identifies it when it matches
        # one of the current plan's tier copays, so another plan's copay for the same tier can be used.
        if claim.member_owes == 0:
            return "rx_preventive", ZERO
        rx = current["in_network"].get("pharmacy") or {}
        for channel in RX_CHANNELS:
            for i, copay in enumerate(rx.get(channel) or []):
                if copay is not None and money(copay) == claim.member_owes:
                    return f"rx:{channel}:{i}", claim.member_owes
        return "rx", claim.member_owes
    services = current["in_network"]["services"]
    copays = [(money(services[s]["copay"]), s) for s in COPAY_SERVICES
              if (services.get(s) or {}).get("rule") == "copay"]
    for amount, svc in copays:
        if claim.member_owes == amount and claim.allowed >= claim.member_owes:
            return svc, ZERO
    if claim.member_owes > 0 and claim.member_owes == claim.allowed and claim.plan_paid == 0:
        # A copay visit whose allowed amount was below the copay: the member paid the allowed amount.
        above = sorted(c for c in copays if c[0] > claim.member_owes)
        if above:
            return above[0][1], ZERO
    if claim.network == "out_of_network":
        # Paid in full out of network: an emergency-related charge bundled under a copay elsewhere.
        if claim.member_owes == 0 and claim.plan_paid >= claim.allowed:
            return "bundled:emergency", ZERO
        return "out_of_network", ZERO
    explicit = SERVICE_BY_CATEGORY.get(claim.category) if claim.category not in ("laboratory", "imaging") else None
    if claim.member_owes == 0 and claim.plan_paid >= claim.allowed:
        # Paid in full: bundled under a copay visit (free today, but not under other plans), a categorised
        # service (e.g. a hospital stay after the OOP maximum was reached), or else preventive care.
        if explicit in COPAY_SERVICES:
            return f"bundled:{explicit}", ZERO
        return explicit or "preventive", ZERO
    if explicit:
        # An explicit category (set by the extractor or during review) wins over payment-pattern guesses.
        return explicit, ZERO
    # Read the current plan's payment pattern: plain coinsurance means no deductible applied.
    coinsurance = Decimal(str(current["in_network"]["coinsurance"]))
    if abs(claim.member_owes - money(coinsurance * claim.allowed)) <= CENT:
        return NO_DEDUCTIBLE_SERVICE, ZERO
    return DEDUCTIBLE_SERVICE, ZERO


# --- replay -----------------------------------------------------------------------------------------

class Accumulators:
    def __init__(self):
        self.ded_member: dict[str, Decimal] = defaultdict(lambda: ZERO)
        self.oop_member: dict[str, Decimal] = defaultdict(lambda: ZERO)
        self.ded_family = ZERO
        self.oop_family = ZERO

    def remaining(self, net: dict, member: str, family: bool) -> tuple[Decimal, Decimal]:
        def rem(limits: dict, used_member: Decimal, used_family: Decimal) -> Decimal:
            ind = money(limits["individual"])
            if not family:
                return max(ZERO, ind - used_member)
            fam = money(limits["family"])
            if limits["family_type"] == "aggregate":
                return max(ZERO, fam - used_family)
            person = money(limits.get("per_person_cap") or limits["individual"])
            return max(ZERO, min(person - used_member, fam - used_family))
        return (rem(net["deductible"], self.ded_member[member], self.ded_family),
                rem(net["oop_max"], self.oop_member[member], self.oop_family))

    def add(self, member: str, deductible: Decimal, cost: Decimal):
        self.ded_member[member] += deductible
        self.ded_family += deductible
        self.oop_member[member] += cost
        self.oop_family += cost


def apply_rule(rule: str, allowed: Decimal, coinsurance: Decimal, copay: Decimal, ded_rem: Decimal) -> tuple[Decimal, Decimal]:
    """Member cost before the OOP cap, and the part applied to the deductible."""
    if rule in ("covered_100", "preventive"):
        return ZERO, ZERO
    if rule == "copay":
        return min(copay, allowed), ZERO
    if rule == "coinsurance_no_deductible":
        return money(coinsurance * allowed), ZERO
    d = min(allowed, ded_rem)
    if rule == "deductible_then_coinsurance":
        return money(d + coinsurance * (allowed - d)), d
    if rule == "deductible_then_covered_100":
        return d, d
    if rule == "rx_deductible_then_copay":
        rest = allowed - d
        return d + (min(copay, rest) if rest > 0 else ZERO), d
    raise ValueError(f"Unknown rule {rule}")


def plan_rule(plan: dict, service: str, copay: Decimal, is_current: bool) -> tuple[str, dict, Decimal, Decimal]:
    """Return (rule, network section used for accumulators, coinsurance, copay)."""
    inn = plan["in_network"]
    if service == "rx_preventive":
        return "covered_100", inn, ZERO, ZERO
    if service.startswith("bundled:"):
        if is_current:
            return "covered_100", inn, ZERO, ZERO
        service = service.split(":", 1)[1]
    if service == "rx" or service.startswith("rx:"):
        rule = "rx_deductible_then_copay" if inn["pharmacy"]["deductible_applies"] else "copay"
        if service.startswith("rx:"):
            _, channel, tier = service.split(":")
            tiers = inn["pharmacy"].get(channel) or []
            if int(tier) < len(tiers) and tiers[int(tier)] is not None:
                copay = money(tiers[int(tier)])
        return rule, inn, ZERO, copay
    if service == "out_of_network":
        oon = plan["out_of_network"]
        return "deductible_then_coinsurance", oon, Decimal(str(oon["coinsurance"])), ZERO
    rule = inn["services"][service]
    return rule["rule"], inn, Decimal(str(inn["coinsurance"])), money(rule.get("copay", 0))


def replay(claims: list[Claim], params: dict, plan_id: str) -> list[Result]:
    plan = params["plans"][plan_id]
    current_id = current_plan_id(params)
    current = params["plans"][current_id]
    family = family_coverage(params)
    accs: dict[tuple[int, str], Accumulators] = defaultdict(Accumulators)
    results = []
    for c in sorted(claims, key=lambda c: (c.year, c.month, c.seq, c.id)):
        if c.coverage_type not in REPLAYED:
            # Dental and vision are separate plans, identical under every medical option.
            results.append(Result(plan_id, c, c.coverage_type, "same_under_all_plans", c.member_owes, ZERO, False))
            continue
        service, copay = classify(c, current)
        rule, net, coins, copay = plan_rule(plan, service, copay, plan_id == current_id)
        acc = accs[(c.year, "out" if net is plan.get("out_of_network") else "in")]
        ded_rem, oop_rem = acc.remaining(net, c.member, family)
        cost, ded = apply_rule(rule, c.allowed, coins, copay, ded_rem)
        cost = min(cost, oop_rem)
        ded = min(ded, cost)
        acc.add(c.member, ded, cost)
        # Amounts the plan never covers are owed under every plan and don't accumulate.
        results.append(Result(plan_id, c, service, rule, cost + c.not_covered, ded, True))
    return results


# --- annual economics -------------------------------------------------------------------------------

def hsa_contributions(params: dict, plan_id: str, cfg: Config) -> tuple[Decimal, Decimal]:
    plan = params["plans"][plan_id]
    if not plan.get("hsa_eligible"):
        return ZERO, ZERO
    family = family_coverage(params)
    employer = money(plan["employer_hsa"]["other_tiers" if family else "self"])
    irs = params["irs_limits"]
    limit = money(irs["hsa_contribution_family" if family else "hsa_contribution_self"])
    if params["tax"].get("age_55_plus"):
        limit += money(irs["hsa_catch_up_55"])
    room = max(ZERO, limit - employer)
    employee = room if cfg.employee_hsa is None else min(money(cfg.employee_hsa), room)
    return employer, employee


def annual_premium(params: dict, plan_id: str) -> Decimal:
    per = money(params["plans"][plan_id]["premium_per_paycheck"][params["coverage_tier"]])
    return money(per * int(params["pay_periods_per_year"]))


def net_cost(params: dict, plan_id: str, oop: Decimal, cfg: Config) -> dict[str, Decimal]:
    rate = combined_tax_rate(params)
    premiums = annual_premium(params, plan_id)
    premium_tax = money(premiums * rate) if cfg.premiums_pre_tax else ZERO
    employer, employee = hsa_contributions(params, plan_id, cfg)
    hsa_tax = money(employee * rate)
    return {"premiums": premiums, "premium_tax_savings": premium_tax, "oop": oop, "employer_hsa": employer,
            "employee_hsa": employee, "hsa_tax_savings": hsa_tax,
            "net": premiums - premium_tax + oop - employer - hsa_tax}


def oop_by_year(results: list[Result]) -> dict[int, Decimal]:
    out: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for r in results:
        if r.replayed:
            out[r.claim.year] += r.cost
    return dict(out)


def full_years(claims: list[Claim]) -> list[int]:
    actual = [c for c in claims if not c.is_estimate] or claims
    first = min((c.year, c.month) for c in actual)
    last = max((c.year, c.month) for c in actual)
    return [y for y in range(first[0], last[0] + 1) if first <= (y, 1) and (y, 12) <= last]


def base_year(claims: list[Claim], cfg: Config) -> int:
    if cfg.base_year:
        return cfg.base_year
    years = full_years(claims)
    return years[-1] if years else max(c.year for c in claims)


def baseline_check(claims: list[Claim], params: dict, cfg: Config) -> list[dict]:
    """CM-6: replaying the current plan should reproduce what was actually owed."""
    current = current_plan_id(params)
    actual_claims = [c for c in claims if not c.is_estimate and c.coverage_type in REPLAYED]
    results = replay(actual_claims, params, current)
    full = set(full_years(claims))
    rows = []
    for year in sorted({c.year for c in actual_claims}):
        actual = sum((c.member_owes + c.not_covered for c in actual_claims if c.year == year), ZERO)
        modeled = sum((r.cost for r in results if r.claim.year == year), ZERO)
        accuracy = 1 - float(abs(modeled - actual) / actual) if actual else 1.0
        rows.append({"year": year, "actual": actual, "modeled": modeled, "accuracy": accuracy,
                     "complete_year": year in full or year == max(c.year for c in actual_claims),
                     "partial_start": year == min(c.year for c in actual_claims) and year not in full,
                     "passes": accuracy >= 1 - cfg.baseline_tolerance})
    return rows


def extra_event(member: str, year: int, allowed: Decimal, month: int = 12) -> Claim:
    return Claim(id="EXTRA_EVENT", year=year, month=month, seq=999, coverage_type="medical", category="inpatient",
                 member=member, network="in_network", allowed=allowed, plan_paid=ZERO, member_owes=ZERO,
                 not_covered=ZERO, source="scenario")


def scenario_claims(claims: list[Claim], year: int, kind: str, cfg: Config, params: dict) -> list[Claim]:
    year_claims = [c for c in claims if c.year == year]
    if kind == "typical":
        return year_claims
    current = params["plans"][current_plan_id(params)]
    if kind == "low":
        return [c for c in year_claims if c.coverage_type not in REPLAYED
                or classify(c, current)[0].startswith("rx") or classify(c, current)[0] == "preventive"]
    if kind == "high":
        members = sorted({c.member for c in year_claims if c.coverage_type in REPLAYED}) or ["Member_A"]
        return year_claims + [extra_event(members[0], year, cfg.high_event_allowed, month=6)]
    raise ValueError(kind)


def scenarios(claims: list[Claim], params: dict, cfg: Config) -> list[dict]:
    year = base_year(claims, cfg)
    rows = []
    for kind in ("low", "typical", "high"):
        sc = scenario_claims(claims, year, kind, cfg, params)
        for pid in params["plans"]:
            oop = oop_by_year(replay(sc, params, pid)).get(year, ZERO)
            rows.append({"scenario": kind, "year": year, "plan": pid, **net_cost(params, pid, oop, cfg)})
    for pid, plan in params["plans"].items():
        oop_max = money(plan["in_network"]["oop_max"]["family" if family_coverage(params) else "individual"])
        rows.append({"scenario": "worst_case", "year": year, "plan": pid, **net_cost(params, pid, oop_max, cfg)})
    return rows


def break_even(claims: list[Claim], params: dict, cfg: Config) -> tuple[list[dict], list[dict]]:
    """CM-13: net cost as extra allowed spending (one in-network inpatient-type event) is added to the base year."""
    year = base_year(claims, cfg)
    base = [c for c in claims if c.year == year]
    members = sorted({c.member for c in base if c.coverage_type in REPLAYED}) or ["Member_A"]
    grid = []
    x = ZERO
    while x <= cfg.breakeven_max:
        sc = base + ([extra_event(members[0], year, x)] if x > 0 else [])
        row = {"extra_allowed": x}
        for pid in params["plans"]:
            row[pid] = net_cost(params, pid, oop_by_year(replay(sc, params, pid)).get(year, ZERO), cfg)["net"]
        grid.append(row)
        x += cfg.breakeven_step
    crossings = []
    plans = list(params["plans"])
    for i, a in enumerate(plans):
        for b in plans[i + 1:]:
            start = grid[0][a] < grid[0][b]
            cross = next((g["extra_allowed"] for g in grid if (g[a] < g[b]) != start), None)
            crossings.append({"cheaper_at_base": a if start else b, "other": b if start else a, "break_even_at": cross})
    return grid, crossings


def cash_flow(claims: list[Claim], params: dict, plan_id: str, cfg: Config) -> list[dict]:
    """CM-15: month-by-month HSA-eligible spending vs. cumulative HSA deposits in the base year."""
    year = base_year(claims, cfg)
    results = [r for r in replay([c for c in claims if c.year == year], params, plan_id)]
    employer, employee = hsa_contributions(params, plan_id, cfg)
    spent = deposits = ZERO
    balance_start = cfg.starting_hsa_balance
    rows = []
    for m in range(1, 13):
        spent += sum((r.cost for r in results if r.claim.month == m), ZERO)
        er = employer if (cfg.employer_hsa_timing == "lump_sum_january" and m == 1) else (
            ZERO if cfg.employer_hsa_timing == "lump_sum_january" else money(employer / 12))
        deposits += er + money(employee / 12)
        balance = balance_start + deposits - spent
        rows.append({"month": m, "cumulative_spending": spent, "cumulative_deposits": deposits,
                     "balance": balance, "shortfall": balance < 0})
    return rows


def projection(claims: list[Claim], params: dict, cfg: Config) -> list[dict]:
    """CM-16: multi-year cumulative savings vs. the current plan and HSA balance (invest whatever is left)."""
    year = base_year(claims, cfg)
    current = current_plan_id(params)
    base = [c for c in claims if c.year == year]
    rows = []
    per_plan = {}
    for pid in params["plans"]:
        res = replay(base, params, pid)
        per_plan[pid] = {"oop": oop_by_year(res).get(year, ZERO),
                         "other": sum((r.cost for r in res if not r.replayed), ZERO)}
    for pid in params["plans"]:
        if not params["plans"][pid].get("hsa_eligible"):
            continue
        balance = cfg.starting_hsa_balance
        cumulative = ZERO
        employer, employee = hsa_contributions(params, pid, cfg)
        for t in range(1, cfg.projection_years + 1):
            med = Decimal(str((1 + cfg.medical_inflation) ** (t - 1)))
            prem = Decimal(str((1 + cfg.premium_inflation) ** (t - 1)))
            nets = {}
            for q in (current, pid):
                n = net_cost(params, q, money(per_plan[q]["oop"] * med), cfg)
                nets[q] = money((n["premiums"] - n["premium_tax_savings"]) * prem
                                + n["oop"] - n["employer_hsa"] - n["hsa_tax_savings"])
            cumulative += nets[current] - nets[pid]
            spending = money((per_plan[pid]["oop"] + per_plan[pid]["other"]) * med)
            balance = money(balance * Decimal(str(1 + cfg.investment_return)) + employer + employee - spending)
            rows.append({"plan": pid, "year_index": t, "plan_year": year + t, "net_current": nets[current],
                         "net_plan": nets[pid], "cumulative_savings": cumulative, "hsa_spending": spending,
                         "hsa_balance_end": balance})
    return rows


def sensitivity(claims: list[Claim], params: dict, cfg: Config,
                factors=(0.5, 0.75, 1.0, 1.25, 1.5, 2.0)) -> list[dict]:
    """CM-17: scale allowed amounts of the base year (copays unchanged) and compare net costs."""
    year = base_year(claims, cfg)
    base = [c for c in claims if c.year == year]
    rows = []
    for f in factors:
        k = Decimal(str(f))
        sc = [replace(c, allowed=money(c.allowed * k)) if c.coverage_type in REPLAYED else c for c in base]
        row = {"utilization_factor": f}
        for pid in params["plans"]:
            row[pid] = net_cost(params, pid, oop_by_year(replay(sc, params, pid)).get(year, ZERO), cfg)["net"]
        rows.append(row)
    return rows


# --- workbook ---------------------------------------------------------------------------------------

def build_workbook(claims: list[Claim], params: dict, cfg: Config, out: Path, sources: dict[str, str]):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    bold = Font(bold=True)
    title_font = Font(bold=True, size=14)
    head_fill = PatternFill("solid", fgColor="DDEBF7")
    edit_fill = PatternFill("solid", fgColor="FFF2CC")
    money_fmt = '"$"#,##0.00;[Red]-"$"#,##0.00'
    pct_fmt = "0.00%"
    plans = list(params["plans"])
    current = current_plan_id(params)
    names = {pid: params["plans"][pid]["name"] for pid in plans}
    year0 = base_year(claims, cfg)
    years = sorted({c.year for c in claims})

    wb = Workbook()

    def sheet(title: str, heading: str):
        ws = wb.create_sheet(title)
        ws["A1"] = f"ESTIMATE - {heading}"
        ws["A1"].font = title_font
        return ws

    def header(ws, row: int, labels: list[str]):
        for i, label in enumerate(labels, start=1):
            c = ws.cell(row=row, column=i, value=label)
            c.font = bold
            c.fill = head_fill
            c.alignment = Alignment(wrap_text=True, vertical="top")

    def widths(ws, w: list[int]):
        for i, v in enumerate(w, start=1):
            ws.column_dimensions[get_column_letter(i)].width = v

    # Parameters ---------------------------------------------------------------------------------
    ws = wb.active
    ws.title = "Parameters"
    ws["A1"] = "ESTIMATE - Editable parameters (yellow cells). Other sheets recalculate from these."
    ws["A1"].font = title_font
    ws["A2"] = ("Changing deductibles, OOP maximums, coinsurance or copays requires re-running hsa_cost_model.py "
                "(the claim replay is computed in Python). Premiums, HSA funding, tax and projection inputs are live.")
    ref: dict[str, str] = {}
    r = 4
    header(ws, r, ["General", "Value", "Notes"])
    general = [
        ("coverage_tier", params["coverage_tier"], "Coverage tier used for premiums and family rules"),
        ("pay_periods", params["pay_periods_per_year"], "Pay periods per year"),
        ("fed", params["tax"]["federal_marginal_rate"], "Federal marginal income-tax rate"),
        ("state", params["tax"]["state_marginal_rate"], "State marginal income-tax rate"),
        ("fica", params["tax"]["fica_rate"], "FICA rate (applies to payroll HSA contributions and pre-tax premiums)"),
        ("payroll", bool(params["tax"].get("hsa_via_payroll")), "HSA contributions through payroll (FICA savings)"),
        ("pretax", cfg.premiums_pre_tax, "Premiums deducted pre-tax (Section 125)"),
        ("combined", None, "Combined marginal rate (formula)"),
        ("employee_hsa", None, "Your annual HSA contribution (applies to HSA plans; capped by the IRS limit)"),
        ("hsa_limit", None, "IRS HSA limit for your tier incl. catch-up if 55+"),
        ("return", cfg.investment_return, "HSA investment return per year (projection)"),
        ("med_infl", cfg.medical_inflation, "Medical cost inflation per year (projection)"),
        ("prem_infl", cfg.premium_inflation, "Premium inflation per year (projection)"),
        ("start_balance", float(cfg.starting_hsa_balance), "HSA balance at the start of year 1"),
        ("tolerance", cfg.baseline_tolerance, "Baseline check tolerance (replay must be within this of actual)"),
    ]
    family = family_coverage(params)
    irs = params["irs_limits"]
    hsa_limit = money(irs["hsa_contribution_family" if family else "hsa_contribution_self"]) + (
        money(irs["hsa_catch_up_55"]) if params["tax"].get("age_55_plus") else ZERO)
    for key, value, note in general:
        r += 1
        ws.cell(row=r, column=1, value=key)
        ws.cell(row=r, column=3, value=note)
        cell = ws.cell(row=r, column=2)
        ref[key] = f"Parameters!$B${r}"
        if key == "combined":
            cell.value = f"={ref['fed']}+{ref['state']}+IF({ref['payroll']},{ref['fica']},0)"
            cell.number_format = pct_fmt
            continue
        if key == "hsa_limit":
            cell.value = float(hsa_limit)
            cell.number_format = money_fmt
            continue
        if key == "employee_hsa":
            hsa_plans = [p for p in plans if params["plans"][p].get("hsa_eligible")]
            room = hsa_contributions(params, hsa_plans[0], cfg)[1] if hsa_plans else ZERO
            value = float(room)
        cell.value = value
        cell.fill = edit_fill
        if key in ("fed", "state", "fica", "return", "med_infl", "prem_infl", "tolerance"):
            cell.number_format = pct_fmt
        elif key in ("employee_hsa", "start_balance"):
            cell.number_format = money_fmt

    r += 2
    header(ws, r, ["Plan parameter"] + [names[p] for p in plans])
    plan_rows = [
        ("premium_per_paycheck", lambda p: p["premium_per_paycheck"][params["coverage_tier"]], money_fmt, True),
        ("employer_hsa", lambda p: p["employer_hsa"]["other_tiers" if family else "self"], money_fmt, True),
        ("hsa_eligible", lambda p: bool(p.get("hsa_eligible")), None, False),
        ("deductible_individual", lambda p: p["in_network"]["deductible"]["individual"], money_fmt, False),
        ("deductible_family", lambda p: p["in_network"]["deductible"]["family"], money_fmt, False),
        ("deductible_family_type", lambda p: p["in_network"]["deductible"]["family_type"], None, False),
        ("oop_max_individual", lambda p: p["in_network"]["oop_max"]["individual"], money_fmt, False),
        ("oop_max_family", lambda p: p["in_network"]["oop_max"]["family"], money_fmt, True),
        ("coinsurance", lambda p: p["in_network"]["coinsurance"], pct_fmt, False),
    ]
    plan_col = {pid: get_column_letter(2 + i) for i, pid in enumerate(plans)}
    for key, getter, fmt, editable in plan_rows:
        r += 1
        ws.cell(row=r, column=1, value=key)
        for i, pid in enumerate(plans):
            v = getter(params["plans"][pid])
            c = ws.cell(row=r, column=2 + i, value=v if v is not None else "")
            if fmt:
                c.number_format = fmt
            if editable:
                c.fill = edit_fill
            ref[f"{key}:{pid}"] = f"Parameters!${plan_col[pid]}${r}"
    widths(ws, [26, 22, 22, 22, 22])
    ws.column_dimensions["C"].width = 60

    def p(key: str, pid: str | None = None) -> str:
        return ref[f"{key}:{pid}"] if pid else ref[key]

    def employee_hsa_formula(pid: str) -> str:
        return f"=IF({p('hsa_eligible', pid)},MAX(0,MIN({p('employee_hsa')},{p('hsa_limit')}-{p('employer_hsa', pid)})),0)"

    def net_block(ws, row: int, pid: str, oop_formula: str, col0: int = 3):
        """Write premiums ... net cost formulas starting at column col0; returns the net cell address."""
        L = lambda k: get_column_letter(col0 + k)  # noqa: E731
        cells = [
            f"={p('premium_per_paycheck', pid)}*{p('pay_periods')}",
            f"=IF({p('pretax')},{L(0)}{row}*{p('combined')},0)",
            oop_formula,
            f"={p('employer_hsa', pid)}",
            employee_hsa_formula(pid),
            f"={L(4)}{row}*{p('combined')}",
            f"={L(0)}{row}-{L(1)}{row}+{L(2)}{row}-{L(3)}{row}-{L(5)}{row}",
        ]
        for k, v in enumerate(cells):
            c = ws.cell(row=row, column=col0 + k, value=v)
            c.number_format = money_fmt
        return f"{L(6)}{row}"

    net_headers = ["Premiums", "Premium tax savings (pre-tax)", "Medical + Rx out-of-pocket", "Employer HSA",
                   "Your HSA contribution", "HSA tax savings", "Net annual cost"]

    # Claims replay ------------------------------------------------------------------------------
    rp = sheet("Claims replay", "Claim-by-claim replay under each plan (values computed by hsa_cost_model.py)")
    header(rp, 3, ["Plan", "Plan year", "Month", "ID", "Coverage", "Category", "Member", "Network", "Source",
                   "Allowed", "Actual owed (current plan)", "Service (inferred)", "Rule", "Modeled member cost",
                   "Applied to deductible", "Replayed"])
    all_results = {pid: replay(claims, params, pid) for pid in plans}
    rr = 3
    for pid in plans:
        for res in all_results[pid]:
            c = res.claim
            rr += 1
            vals = [pid, c.year, c.month, c.id, c.coverage_type, c.category, c.member, c.network, c.source,
                    float(c.allowed), float(c.member_owes), res.service, res.rule, float(res.cost),
                    float(res.deductible_applied), "yes" if res.replayed else "no"]
            for k, v in enumerate(vals, start=1):
                cell = rp.cell(row=rr, column=k, value=v)
                if k in (10, 11, 14, 15):
                    cell.number_format = money_fmt
    last = rr
    rp.freeze_panes = "A4"
    rp.auto_filter.ref = f"A3:P{last}"
    widths(rp, [10, 9, 7, 11, 10, 20, 11, 15, 18, 11, 12, 22, 26, 12, 12, 9])
    R = lambda col: f"'Claims replay'!${col}$4:${col}${last}"  # noqa: E731

    # Annual comparison --------------------------------------------------------------------------
    ac = sheet("Annual comparison", "Net annual cost by plan year (formulas)")
    ac["A2"] = ("Net = premiums - premium tax savings + modeled medical/Rx out-of-pocket - employer HSA - tax savings "
                "on your HSA contribution. Dental and vision are excluded (identical under every plan).")
    header(ac, 4, ["Plan year", "Plan"] + net_headers + ["vs. current plan", "Coverage of year"])
    ar = 4
    span = defaultdict(set)
    for c in claims:
        if not c.is_estimate:
            span[c.year].add(c.month)
    current_net_cell = {}
    for y in years:
        for pid in plans:
            ar += 1
            ac.cell(row=ar, column=1, value=y)
            ac.cell(row=ar, column=2, value=names[pid])
            oop = f'=SUMIFS({R("N")},{R("A")},"{pid}",{R("B")},{y},{R("P")},"yes")'
            net = net_block(ac, ar, pid, oop)
            if pid == current:
                current_net_cell[y] = net
            months = sorted(span.get(y, set()))
            ac.cell(row=ar, column=11, value=(f"claims in {MONTHS[months[0] - 1]}-{MONTHS[months[-1] - 1]}"
                                              if months else "estimates only"))
        for k in range(len(plans)):
            row = ar - len(plans) + 1 + k
            c = ac.cell(row=row, column=10, value=f"=I{row}-{current_net_cell[y]}")
            c.number_format = money_fmt
    widths(ac, [10, 26, 13, 15, 15, 13, 14, 13, 15, 15, 20])

    # Baseline check ------------------------------------------------------------------------------
    bc = sheet("Baseline check", f"Replay of the current plan ({names[current]}) vs. what was actually owed")
    bc["A2"] = ("Actual claims only (no estimates). A partial first year is excluded from pass/fail because "
                "deductible and out-of-pocket accumulators from earlier months are unknown.")
    header(bc, 4, ["Plan year", "Actual owed", "Modeled", "Accuracy", "Within tolerance?", "Note"])
    br = 4
    for row in baseline_check(claims, params, cfg):
        br += 1
        bc.cell(row=br, column=1, value=row["year"])
        bc.cell(row=br, column=2, value=float(row["actual"])).number_format = money_fmt
        bc.cell(row=br, column=3, value=float(row["modeled"])).number_format = money_fmt
        bc.cell(row=br, column=4, value=f"=IF(B{br}=0,1,1-ABS(C{br}-B{br})/B{br})").number_format = pct_fmt
        if row["partial_start"]:
            bc.cell(row=br, column=5, value="n/a (partial year)")
            bc.cell(row=br, column=6, value="Earlier-year accumulators not in the data")
        else:
            bc.cell(row=br, column=5, value=f'=IF(D{br}>=1-{p("tolerance")},"PASS","FAIL")')
    widths(bc, [10, 14, 14, 11, 18, 44])

    # Scenarios -----------------------------------------------------------------------------------
    sc = sheet("Scenarios", f"Low / typical / high utilization and worst case (base year {year0})")
    sc["A2"] = (f"Low = base-year pharmacy and preventive claims only. Typical = base-year claims (actual + estimates). "
                f"High = typical + one ${cfg.high_event_allowed:,.0f} in-network inpatient event. "
                "Worst case = the plan's in-network family out-of-pocket maximum.")
    header(sc, 4, ["Scenario", "Plan"] + net_headers + ["vs. current plan"])
    srow = 4
    scen = scenarios(claims, params, cfg)
    for kind in ("low", "typical", "high", "worst_case"):
        cur_cell = None
        start = srow + 1
        for row in [s for s in scen if s["scenario"] == kind]:
            srow += 1
            sc.cell(row=srow, column=1, value=kind)
            sc.cell(row=srow, column=2, value=names[row["plan"]])
            oop = f"={p('oop_max_family', row['plan'])}" if kind == "worst_case" and family else (
                f"={p('oop_max_individual', row['plan'])}" if kind == "worst_case" else float(row["oop"]))
            net = net_block(sc, srow, row["plan"], oop)
            if row["plan"] == current:
                cur_cell = net
        for k in range(start, srow + 1):
            sc.cell(row=k, column=10, value=f"=I{k}-{cur_cell}").number_format = money_fmt
    widths(sc, [12, 26, 13, 15, 15, 13, 14, 13, 15, 15])

    # Break-even ----------------------------------------------------------------------------------
    be = sheet("Break-even", f"Net annual cost as extra allowed spending is added to {year0} (values)")
    grid, crossings = break_even(claims, params, cfg)
    be["A2"] = "Extra spending is modeled as one in-network inpatient-type event for one member late in the year."
    header(be, 4, ["Cheaper with no extra spending", "Other plan", "Break-even extra allowed spending"])
    brow = 4
    for x in crossings:
        brow += 1
        be.cell(row=brow, column=1, value=names[x["cheaper_at_base"]])
        be.cell(row=brow, column=2, value=names[x["other"]])
        be.cell(row=brow, column=3, value=float(x["break_even_at"]) if x["break_even_at"] is not None
                else f"none up to ${cfg.breakeven_max:,.0f}").number_format = money_fmt
    brow += 2
    header(be, brow, ["Extra allowed spending"] + [names[p_] for p_ in plans])
    for g in grid:
        brow += 1
        be.cell(row=brow, column=1, value=float(g["extra_allowed"])).number_format = money_fmt
        for i, pid in enumerate(plans):
            be.cell(row=brow, column=2 + i, value=float(g[pid])).number_format = money_fmt
    widths(be, [30, 26, 30, 22])

    # Cash flow -----------------------------------------------------------------------------------
    cf = sheet("Cash flow", f"HSA cash flow by month in {year0} (values; employer funding {cfg.employer_hsa_timing})")
    cf["A2"] = ("Spending = modeled medical/Rx out-of-pocket plus dental and vision costs (all HSA-eligible). "
                "Deposits = employer + your contributions. A shortfall month means you'd pay from other funds "
                "and can reimburse yourself from the HSA later.")
    crow = 3
    for pid in plans:
        if not params["plans"][pid].get("hsa_eligible"):
            continue
        crow += 1
        cf.cell(row=crow, column=1, value=names[pid]).font = bold
        crow += 1
        header(cf, crow, ["Month", "Cumulative spending", "Cumulative deposits", "HSA balance", "Shortfall?"])
        for row in cash_flow(claims, params, pid, cfg):
            crow += 1
            cf.cell(row=crow, column=1, value=MONTHS[row["month"] - 1])
            for k, key in enumerate(("cumulative_spending", "cumulative_deposits", "balance"), start=2):
                cf.cell(row=crow, column=k, value=float(row[key])).number_format = money_fmt
            cf.cell(row=crow, column=5, value="SHORTFALL" if row["shortfall"] else "")
        crow += 1
    widths(cf, [26, 20, 20, 16, 12])

    # Projection ----------------------------------------------------------------------------------
    pj = sheet("Projection", f"{cfg.projection_years}-year projection from {year0} (formulas; invest any HSA balance)")
    pj["A2"] = ("Each year repeats the base year's modeled costs, inflated by the medical and premium inflation rates. "
                "HSA: balance grows at the investment return; yearly deposits are added and HSA-eligible spending "
                "(medical, Rx, dental, vision) is paid from it. IRS limits are held at today's values.")
    oop_cells = {}
    prow = 3
    header(pj, prow, ["Base-year input", "Value"])
    for pid in plans:
        prow += 1
        pj.cell(row=prow, column=1, value=f"{names[pid]}: medical + Rx out-of-pocket")
        pj.cell(row=prow, column=2, value=f'=SUMIFS({R("N")},{R("A")},"{pid}",{R("B")},{year0},{R("P")},"yes")').number_format = money_fmt
        oop_cells[pid] = f"$B${prow}"
        prow += 1
        pj.cell(row=prow, column=1, value=f"{names[pid]}: dental + vision")
        pj.cell(row=prow, column=2, value=f'=SUMIFS({R("N")},{R("A")},"{pid}",{R("B")},{year0},{R("P")},"no")').number_format = money_fmt
        oop_cells[f"{pid}:other"] = f"$B${prow}"
    for pid in plans:
        if not params["plans"][pid].get("hsa_eligible"):
            continue
        prow += 2
        pj.cell(row=prow, column=1, value=f"{names[pid]} vs. {names[current]}").font = bold
        prow += 1
        header(pj, prow, ["Year", "Current plan net", f"{names[pid]} net", "Savings this year", "Cumulative savings",
                          "HSA-eligible spending", "HSA deposits", "HSA balance (end of year)"])
        first_row = prow + 1
        for t in range(1, cfg.projection_years + 1):
            prow += 1
            med = f"(1+{p('med_infl')})^(A{prow}-1)"
            prem = f"(1+{p('prem_infl')})^(A{prow}-1)"

            def net_f(q: str) -> str:
                return (f"({p('premium_per_paycheck', q)}*{p('pay_periods')}*(1-IF({p('pretax')},{p('combined')},0)))*{prem}"
                        f"+{oop_cells[q]}*{med}-{p('employer_hsa', q)}-{employee_hsa_formula(q)[1:]}*{p('combined')}")
            pj.cell(row=prow, column=1, value=t)
            pj.cell(row=prow, column=2, value=f"={net_f(current)}")
            pj.cell(row=prow, column=3, value=f"={net_f(pid)}")
            pj.cell(row=prow, column=4, value=f"=B{prow}-C{prow}")
            pj.cell(row=prow, column=5, value=f"=D{prow}" if t == 1 else f"=E{prow - 1}+D{prow}")
            pj.cell(row=prow, column=6, value=f"=({oop_cells[pid]}+{oop_cells[pid + ':other']})*{med}")
            pj.cell(row=prow, column=7, value=f"={p('employer_hsa', pid)}+{employee_hsa_formula(pid)[1:]}")
            prev = p("start_balance") if t == 1 else f"H{prow - 1}"
            pj.cell(row=prow, column=8, value=f"={prev}*(1+{p('return')})+G{prow}-F{prow}")
            for k in range(2, 9):
                pj.cell(row=prow, column=k).number_format = money_fmt
        assert first_row <= prow
    widths(pj, [44, 18, 18, 16, 18, 20, 14, 22])

    # Sensitivity ---------------------------------------------------------------------------------
    se = sheet("Sensitivity", f"Net annual cost when {year0} allowed amounts are scaled (values)")
    se["A2"] = "Factor 1.0 = the typical scenario. Copays are not scaled."
    header(se, 4, ["Utilization factor"] + [names[x] for x in plans])
    srow = 4
    for row in sensitivity(claims, params, cfg):
        srow += 1
        se.cell(row=srow, column=1, value=row["utilization_factor"])
        for i, pid in enumerate(plans):
            se.cell(row=srow, column=2 + i, value=float(row[pid])).number_format = money_fmt
    widths(se, [18, 26, 26, 26])

    # Historical ----------------------------------------------------------------------------------
    hi = sheet("Historical", "Historical spending by plan year and coverage type (from claims_anonymized.csv)")
    header(hi, 3, ["Plan year", "Coverage", "Data basis", "Claims", "Allowed", "Plan paid", "Member owed"])
    agg: dict[tuple, list] = defaultdict(lambda: [0, ZERO, ZERO, ZERO])
    for c in claims:
        a = agg[(c.year, c.coverage_type, "estimate" if c.is_estimate else "actual")]
        a[0] += 1
        a[1] += c.allowed
        a[2] += c.plan_paid
        a[3] += c.member_owes + c.not_covered
    hrow = 3
    for key in sorted(agg):
        hrow += 1
        vals = [*key, agg[key][0], float(agg[key][1]), float(agg[key][2]), float(agg[key][3])]
        for k, v in enumerate(vals, start=1):
            cell = hi.cell(row=hrow, column=k, value=v)
            if k >= 5:
                cell.number_format = money_fmt
    widths(hi, [10, 12, 12, 8, 14, 14, 14])

    # Assumptions ---------------------------------------------------------------------------------
    asn = sheet("Assumptions", "Assumptions that drive the result")
    notes = [
        "All figures are estimates for decision support, not tax or insurance advice.",
        f"Claims: {sources['claims']} ({len(claims)} rows; {sum(c.is_estimate for c in claims)} are estimates).",
        f"Plan parameters: {sources['plans']}.",
        f"Base year for scenarios, break-even, cash flow, projection and sensitivity: {year0} (latest complete year).",
        f"Current plan ({names[current]}) produced the historical claims; its actual copays identify each claim's "
        "service type (PCP / specialist / urgent care / ER) and each prescription's copay tier.",
        "Other medical claims are typed by the current plan's payment pattern: exactly the coinsurance owed -> "
        "no-deductible service (lab/X-ray); otherwise deductible-then-coinsurance (imaging/outpatient). Claims with "
        "$0 owed and paid in full are: bundled under a visit's copay when categorised as a copay service or "
        "out-of-network (free today; deductible + coinsurance under other plans); their category's rule when "
        "categorised otherwise; else preventive (free under every plan).",
        "Prescriptions: the copay paid today identifies the tier (retail 30-day / 90-day / mail order); each plan's "
        "own copay for that tier is used (the historical copay if no tier matches). HSA plans charge the full "
        "allowed cost until the combined medical deductible is met. $0 prescriptions are treated as ACA preventive.",
        "Out-of-network claims use each plan's out-of-network deductible, OOP maximum and coinsurance "
        "(separate accumulators), except urgent care / ER copays.",
        "Allowed (negotiated) amounts are assumed unchanged under every plan (same Cigna network).",
        "Not-covered amounts are owed under every plan and do not count toward deductibles.",
        "Claims are processed in (month, sequence) order; order within a month is approximate.",
        "Dental and vision are separate plans, identical under every medical option: excluded from net cost, "
        "included in HSA cash flow and projection as HSA-eligible spending.",
        "Pre-tax premiums and payroll HSA contributions save federal + state + FICA at the marginal rates.",
        f"Employer HSA funding timing: {cfg.employer_hsa_timing}.",
        "The projection repeats the base year with inflation; real years vary (see Scenarios and Break-even).",
    ]
    estimated_types = sorted({c.coverage_type for c in claims if c.is_estimate})
    if estimated_types:
        notes.append(f"Some {', '.join(estimated_types)} costs are user-entered estimates, not actual claims "
                     "(source = estimate); calibrate them against Cigna's EOB running totals where possible.")
    for a in params.get("assumptions", []):
        notes.append(f"Plan parameters - {a['path']}: {a['note']}")
    for i, n in enumerate(notes, start=3):
        asn.cell(row=i, column=1, value=f"{i - 2}. {n}")
    asn.column_dimensions["A"].width = 160

    order = ["Annual comparison", "Scenarios", "Break-even", "Cash flow", "Projection", "Sensitivity",
             "Baseline check", "Historical", "Claims replay", "Parameters", "Assumptions"]
    wb._sheets = [wb[n] for n in order]
    wb.active = 0
    wb.save(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Replay de-identified claims under each plan and write an Excel comparison.")
    ap.add_argument("--claims", type=Path, nargs="+", required=True,
                    help="claims_anonymized.csv from the Phase 1 extractor (several files are combined)")
    ap.add_argument("--plans", type=Path, required=True, help="Validated plan-parameters JSON")
    ap.add_argument("--output", type=Path, required=True, help="Workbook to write (.xlsx)")
    ap.add_argument("--employee-hsa", type=Decimal, help="Your annual HSA contribution (default: the maximum allowed)")
    ap.add_argument("--employer-hsa-timing", choices=("per_paycheck", "lump_sum_january"), default="per_paycheck")
    ap.add_argument("--post-tax-premiums", action="store_true", help="Premiums are deducted after tax")
    ap.add_argument("--return", dest="ret", type=float, default=0.06, help="HSA investment return (default 0.06)")
    ap.add_argument("--medical-inflation", type=float, default=0.05)
    ap.add_argument("--premium-inflation", type=float, default=0.05)
    ap.add_argument("--years", type=int, default=5, help="Projection years (default 5)")
    ap.add_argument("--high-event", type=Decimal, default=Decimal("20000"), help="Allowed amount of the high scenario's event")
    ap.add_argument("--tolerance", type=float, default=0.10, help="Baseline tolerance (default 0.10 = within 10%%)")
    ap.add_argument("--base-year", type=int)
    args = ap.parse_args()
    cfg = Config(employee_hsa=args.employee_hsa, employer_hsa_timing=args.employer_hsa_timing,
                 premiums_pre_tax=not args.post_tax_premiums, investment_return=args.ret,
                 medical_inflation=args.medical_inflation, premium_inflation=args.premium_inflation,
                 projection_years=args.years, high_event_allowed=args.high_event,
                 baseline_tolerance=args.tolerance, base_year=args.base_year)
    try:
        params = load_params(args.plans)
        gaps = missing_inputs(params)
        if gaps:
            raise ValueError("The plan parameters are missing values the model needs: " + ", ".join(gaps))
        current_plan_id(params)
        claims = load_claims_files(args.claims, int(params.get("plan_year_start_month", 1)))
        build_workbook(claims, params, cfg, args.output,
                       {"claims": ", ".join(p.name for p in args.claims), "plans": args.plans.name})
    except (OSError, ValueError, KeyError, csv.Error, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    year = base_year(claims, cfg)
    print(f"Workbook -> {args.output}")
    print(f"Base year {year}. Net annual cost (typical scenario):")
    for row in [s for s in scenarios(claims, params, cfg) if s["scenario"] == "typical"]:
        print(f"  {params['plans'][row['plan']]['name']:28s} ${row['net']:>10,.2f}")
    for row in baseline_check(claims, params, cfg):
        status = "partial year" if row["partial_start"] else ("PASS" if row["passes"] else "FAIL")
        print(f"Baseline {row['year']}: modeled ${row['modeled']:,.2f} vs actual ${row['actual']:,.2f} "
              f"({row['accuracy']:.1%}) {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
