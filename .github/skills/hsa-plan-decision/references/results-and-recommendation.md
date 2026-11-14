# Phases 4-5 - Run, check, and explain the result

## Run

```
python hsa_cost_model/hsa_cost_model.py --claims .local/output/claims_anonymized.csv --plans .local/plan_parameters.json --output .local/output/hsa_comparison.xlsx [--employee-hsa AMOUNT] [--tolerance 0.10]
```

Ask before running: how much would they contribute to the HSA (default: the legal maximum minus employer money), and how close the replay must match reality (default within 10%). Other options: `--employer-hsa-timing lump_sum_january`, `--post-tax-premiums`, `--return`, `--medical-inflation`, `--years`, `--high-event`, `--base-year`.

## Baseline check (gate)

The terminal prints, per year, the current plan's modeled vs actual member cost. A partial first year is shown but not judged. If a complete year fails:

1. Wrong `current_plan`, or the current plan's copays/coinsurance in the JSON differ from what was charged that year (plans change every year; use the rules in force for the claims' year if they differ, and say so).
2. Plan year not starting in January: set `plan_year_start_month`.
3. Claims typed wrongly: in the workbook's **Claims replay** sheet (de-identified), filter the current plan and compare "Actual owed" with "Modeled member cost" by "Service (inferred)". An explicit category (anything but `medical_unspecified`, `laboratory`, `imaging`) overrides the model's payment-pattern guess, so the person can set the right category in `review_candidates.csv` and finalize again; or adjust the plan rules if they were wrong.
4. Out-of-network claims or accumulator order: small, explainable differences are acceptable; say so.
5. Missing claims don't break the baseline (both sides lack them) but show up in the completeness check.

Never tune the model until it matches; fix inputs, and explain any remaining gap.

**Estimates only (no claim records):** there is nothing to check the replay against, so no baseline is printed. Say plainly that the result is only as good as the utilization estimates, and lean on the Scenarios, Break-even and Sensitivity sheets.

## Reading the workbook with the person

Open with the answer, then the evidence. Use the **Annual comparison**, **Scenarios** and **Break-even** sheets.

- **Net annual cost** = premiums − tax saved on pre-tax premiums + modeled medical/Rx out-of-pocket − employer HSA money − tax saved on their own HSA contribution. Their own contribution is savings, not a cost. A **negative** net cost means employer money plus tax savings exceed premiums and medical costs; explain that the tax savings only happen if they actually make the contribution.
- **Typical year** (each complete historical year), **low**, **high** (one large event), **worst case** (OOP maximum). An HDHP that wins the worst case is a robust choice.
- **Break-even**: extra allowed spending at which another plan becomes cheaper; "none" means one plan wins at every level tested.
- **Between several HDHPs**: compare typical and worst case. Lower premium + per-person OOP cap tends to win single big events; lower deductible / coinsurance with an aggregate family deductible can win years where several members have moderate costs.
- **Cash flow** sheet: months where HSA deposits don't cover bills. Ask whether they have savings to bridge an early-year deductible.
- **Projection**: cumulative savings and invested HSA balance under their contribution, return and inflation assumptions.
- **Sensitivity**: does the winner change if care use is half or double?

## Caveats to state (when they apply)

- Eligibility: no Medicare, no general-purpose FSA (including the spouse's), no other non-HDHP coverage, not someone's tax dependent. Turning 65 or starting Social Security mid-year matters.
- Dependents: HSA money is tax-free only for themselves, a spouse and tax dependents; non-dependent adult children can have their own HSA.
- Estimates: which numbers came from `estimates.csv`, and how much they move the result.
- Network: the model assumes the same providers and negotiated rates on every plan; check that their doctors are in each plan's network.
- Contributions: if they can't afford to contribute, re-run with `--employee-hsa 0` and show the difference.
- One unusual year (surgery, birth) can dominate; show the high and worst-case rows.

## Recommendation format

1. One sentence: which plan, and whether to fund the HSA.
2. A small table: typical year, a heavier year, worst case per plan.
3. Why (2-4 bullets), and what would change the answer (e.g. "if your family's costs double, Plan B becomes cheaper by about X").
4. Checks before enrolling (eligibility, network, cash buffer, dependents).
5. "This is decision support from your data, not tax, insurance or financial advice. Every figure is an estimate."

If the difference between options is small (a few hundred dollars), say it's close and let non-financial factors (network, predictability, cash on hand) decide.
