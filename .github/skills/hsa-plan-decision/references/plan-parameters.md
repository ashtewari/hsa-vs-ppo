# Phase 2 - Plan parameters

Produce `.local/plan_parameters.json` describing **every** option (including the current plan), in the schema of `plan_parameters/plan_parameters.example.json`, then validate it.

## Where the numbers come from

| Need | Best source | Fallbacks |
|---|---|---|
| Deductible, OOP maximum, coinsurance, copays, Rx tiers | Summary of Benefits and Coverage (SBC), one per plan, standardized 8 pages | Benefits guide comparison table; insurer plan certificate ("Schedule of Benefits") |
| Embedded vs aggregate family deductible / OOP | SBC "Are there other deductibles?" / footnotes, certificate "Family Maximum Calculation" | Benefits guide footnotes ("individual within a family", "non-embedded", "true family"); ask HR |
| Premiums per paycheck by tier | Open-enrollment rate sheet, benefits portal enrollment screen | Pay stub (current plan only) |
| Employer HSA contribution and timing | Benefits guide / HSA page | HR |
| IRS limits for the plan year | irs.gov: search "HSA inflation adjusted amounts" + year (a Revenue Procedure) | Employer slides often quote the contribution limits |

Ask the person to download the documents into `.local/plans/`. Plan documents normally contain no personal data, so you may read them; confirm first if a file might be an enrollment confirmation.

## Field-by-field

- `coverage_tier`: `employee`, `employee_spouse`, `employee_children`, `family`. Family rules (family deductible, embedded/aggregate) apply to every tier except `employee`.
- `pay_periods_per_year`: 52 / 26 / 24 / 12. If the sheet shows monthly premiums and they're paid twice a month, convert (monthly x 12 / 24), and record it in `assumptions`.
- `current_plan`: the plan id that produced their claims history. Required when there isn't exactly one non-HSA plan (e.g. they're on an HDHP now, or choosing between two PPOs). If they have no claims at all, use the plan their estimates are priced under.
- `plans.<id>.premium_per_paycheck`: fill all four tiers if available; at least their tier.
- `employer_hsa`: `self` (employee-only tier) and `other_tiers`. `0` for non-HSA plans.
- `in_network.deductible` / `oop_max`: `individual`, `family`, `family_type` (`embedded` or `aggregate`), `per_person_cap` (e.g. "$3,500 max per person in a family"; else `null`).
- `coinsurance`: **the member's share** as a decimal. "Plan pays 80%" means `0.20`. "90%, copays apply" means `0.10`.
- `services` (each one of these rules):

| SBC wording | rule |
|---|---|
| "No charge", "Covered at 100%" (preventive) | `covered_100` |
| "$25 copay", "$25 copay, then 100%", "$25 copay, deductible does not apply" | `copay` with `copay: 25` |
| "20% coinsurance", "10%" with no deductible mentioned **and** the SBC answers "deductible does not apply" | `coinsurance_no_deductible` |
| "Deductible, then 20%", "20% after deductible" | `deductible_then_coinsurance` |
| "Deductible, then 100%", "No charge after deductible" | `deductible_then_covered_100` |

  Map: `office_visit_pcp`, `office_visit_specialist`, `urgent_care`, `emergency`, `xray_lab` (diagnostic test, X-ray, blood work), `complex_imaging` (CT/MRI/PET), `inpatient` (hospital stay), `outpatient_hospital` (outpatient surgery/facility), `preventive`. If a service has a copay *plus* coinsurance, use the dominant one and record the simplification.
- `pharmacy`: `deductible_applies` (HDHPs: almost always `true`; "Deductible, then $10" means true), and `[tier1, tier2, tier3]` copays for `retail_30`, `retail_90`, `mail_90`. The model identifies each historical fill's tier by matching the copay paid against the **current** plan's arrays and then uses each plan's own copay for that tier, so the current plan's arrays must be exact. If a plan uses Rx coinsurance instead of copays, the model can't represent it: put the typical dollar amount, record the simplification, and say the Rx part of that plan is approximate.
- `out_of_network`: deductible, OOP, coinsurance (member share). The model needs numbers. For plans with **no** out-of-network coverage (HMO/EPO), use deductible 0, coinsurance `1.0`, OOP maximum `999999` (member pays everything), and record that in `assumptions`.
- `tax`: from the interview. `healthcare_fsa_annual`: `0` if none.
- `assumptions`: one entry per interpretation, with the source document and page.

## Common traps

- Coinsurance written as the plan's share. Always convert to the member's share.
- "Deductible" on an HDHP family tier: under the IRS rules for family coverage, an embedded per-person deductible can't be below the family minimum; the validator reports it. That usually means the plan is aggregate.
- The premium sheet shows *annual* or *monthly* figures while the schema wants per paycheck.
- An HSA plan whose name doesn't say HSA: check the SBC for "HSA-eligible" / "qualified HDHP".
- Rx handled by a separate pharmacy benefit manager: the rules are still in the medical plan's SBC or a separate Rx summary.
- Last year's SBC: plan values change yearly; use the plan-year documents, and say which year you used.

## Validate

```
python plan_parameters/validate_plan_parameters.py .local/plan_parameters.json
```

Read back to the person, per plan: annual premium for their tier, deductible, OOP maximum, coinsurance, employer HSA. Ask "does this match your paperwork?" before moving on.
