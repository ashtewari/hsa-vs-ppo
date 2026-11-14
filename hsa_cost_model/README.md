# HSA vs. PPO cost comparison model (Phase 2)

Replays your de-identified claims (`claims_anonymized.csv` from the [Phase 1 extractor](../cigna_eob_local/README.md)) under every plan in a validated [plan-parameters file](../plan_parameters/), then adds premiums, employer HSA money and tax effects, and writes an Excel workbook. Everything runs locally and offline. **Every figure is an estimate.**

## Install

From the repository root (see the [top-level README](../README.md) for the virtual environment):

```powershell
python -m pip install -r requirements.txt   # PyMuPDF + openpyxl
```

## Run

```powershell
python hsa_cost_model\hsa_cost_model.py `
  --claims .local\output\claims_anonymized.csv `
  --plans .local\plan_parameters.json `
  --output .local\output\hsa_comparison.xlsx
```

Options (defaults in brackets): `--claims` accepts several files (for example a PDF-only run plus a CSV run; they are combined), `--employee-hsa` [the maximum allowed], `--employer-hsa-timing per_paycheck|lump_sum_january` [per_paycheck], `--post-tax-premiums` [pre-tax], `--return` [0.06], `--medical-inflation` [0.05], `--premium-inflation` [0.05], `--years` [5], `--high-event` [20000], `--tolerance` [0.10], `--base-year` [latest complete year]. Before running, the script lists any plan-parameter values it needs that are still `null`.

The terminal prints each plan's typical-year net cost and the baseline check.

## How the replay works

- Claims are processed in `(plan year, month, sequence)` order. Deductible and out-of-pocket accumulators reset every plan year and are tracked per member and per family. Each plan's limits are applied as embedded or aggregate, with any per-person cap. In-network and out-of-network accumulators are separate.
- Each claim's service type is inferred from what the **current plan** (the one non-HSA plan) actually charged:
  - Copay amounts identify PCP, specialist, urgent care or ER visits, including visits where the allowed amount was below the copay.
  - Exactly the coinsurance → lab/X-ray (no deductible). Anything else → deductible-then-coinsurance.
  - $0 owed and paid in full → bundled under the visit's copay if the claim's category is a copay service, or if it's out-of-network (free today; deductible and coinsurance under other plans). Other categories (for example a hospital stay after the OOP maximum was reached) use their own rule; uncategorized claims are treated as preventive.
  - An explicit category (other than lab/imaging) wins over the payment-pattern guess, so fixing categories during review changes the replay.
  - For prescriptions, the copay actually paid identifies the tier (retail 30-day / 90-day / mail order) when it matches one of the current plan's tier copays; each plan's own copay for that tier is then used. Unmatched copays are carried over unchanged. Under an HSA plan the full allowed cost is paid until the deductible is met.
- Set `current_plan` in the plan parameters if the plan that produced the history is an HDHP, or if there are several non-HSA options.
- Dental and vision are identical under every option. They are excluded from net cost but included as HSA-eligible spending in cash flow and the projection.

Net annual cost = premiums − tax saved on pre-tax premiums + modeled medical/Rx out-of-pocket − employer HSA − tax saved on your HSA contribution. Your own HSA contribution is not a cost.

## Workbook

| Sheet | Contents | Live formulas? |
|---|---|---|
| Annual comparison | Net cost per plan per plan year, vs. the current plan, with a chart of the base year's net cost per plan | ✅ |
| Scenarios | Low / typical / high utilization and worst case (OOP maximum) | ✅ (out-of-pocket from the replay) |
| Break-even | Net cost as extra allowed spending is added; pairwise break-even points | values |
| Cash flow | Month-by-month HSA deposits vs. HSA-eligible spending; shortfall months | values |
| Projection | Multi-year cumulative savings and invested HSA balance | ✅ |
| Sensitivity | Net cost with base-year utilization × 0.5 … 2.0 | values |
| Baseline check | Replay of the current plan vs. what was actually owed (CM-6) | ✅ |
| Historical, Claims replay | Inputs and claim-by-claim detail | values |
| Parameters | Editable inputs (yellow) | — |
| Assumptions | Everything that drives the result | — |

Edit yellow cells on **Parameters** to change premiums, employer HSA money, your contribution, tax rates, inflation or the investment return; the formula sheets recalculate. Deductibles, OOP maximums, coinsurance and copays drive the claim replay, so change those in the plan-parameters JSON and re-run the script.

## Tests

```powershell
python -m pytest hsa_cost_model -q
```

The tests are hand-calculated cases: a deductible crossed mid-claim, the OOP-maximum cap, the preventive exemption, embedded and aggregate family deductibles, plan-year resets, Rx deductibles, service inference, the net-cost layers, break-even, and workbook structure (including the chart). To see a complete run on synthetic data, use the [demo](../demo/README.md).
