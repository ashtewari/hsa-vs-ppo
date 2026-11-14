# HSA vs. PPO decision toolkit

Should you switch from your PPO to a high-deductible health plan (HDHP) with a health savings account (HSA)? This toolkit answers that with **your own Cigna claims history** instead of a generic premium-vs-deductible comparison. Everything runs **offline on your computer**.

1. **[EOB extractor](./cigna_eob_local/README.md) (Phase 1).** Turns myCigna claim-summary CSV exports and Explanation of Benefits (EOB) PDFs into **de-identified** financial CSVs. Names, member IDs, providers, drugs, claim numbers and exact dates never reach the output. A review step lets you check every doubtful figure, and a leak scan confirms the outputs are clean.
2. **[Cost comparison model](./hsa_cost_model/README.md) (Phase 2).** Replays those claims, in order, under every plan's deductible, coinsurance, copay and out-of-pocket rules. It then adds premiums, employer HSA money and tax savings, and writes an Excel workbook: net annual cost, scenarios, break-even, worst case, monthly cash flow and a multi-year projection.

The full requirements are in [PRD.md](./PRD.md).

> [!IMPORTANT]
> **Disclaimer.** This is a personal decision-support tool, provided as is under the [MIT License](./LICENSE). It is **not** medical, tax, legal, insurance or financial advice, and every figure it produces is an estimate. Check results against your plan documents and the IRS rules for your plan year. This project is **not affiliated with, endorsed by, or sponsored by Cigna**; "Cigna" and "myCigna" are trademarks of their owners and are used only to describe the file formats the tool reads.

## Privacy model

- No network access, cloud APIs, AI models, analytics or telemetry at runtime. `pip install` is the only network activity.
- Outputs are built from an allow-list of fields, not by redacting raw text. Raw PDF text stays in memory.
- Your real files belong in a **`.local/` folder** in your clone. It is gitignored and blocked by the pre-commit privacy guard. Put your claim CSVs, EOB PDFs, estimates, plan documents, plan-parameters file and all outputs there.
- Only de-identified aggregates are meant to be shared, in tiers. See [PRD §10](./PRD.md#10-data-sharing-policy).

## Quick start (Windows PowerShell)

Requires Python 3.10+. macOS/Linux work too: use `.venv/bin/python` and `/` paths.

```powershell
git clone https://github.com/ashtewari/hsa-vs-ppo.git
cd hsa-vs-ppo
py -m venv .venv
.\.venv\Scripts\Activate.ps1          # or call .\.venv\Scripts\python.exe directly
python -m pip install -r requirements.txt
git config core.hooksPath .githooks   # enable the privacy guard (recommended if you might commit)
```

**1. Gather your data** into `.local\` (see the [extractor guide](./cigna_eob_local/README.md#2-gather-your-files)): myCigna claim-summary CSVs (medical, dental, pharmacy), medical EOB PDFs, and an optional `estimates.csv` for costs with no claim record.

**2. Extract, review, finalize:**

```powershell
python cigna_eob_local\cigna_eob_extract.py extract `
  --claims-csv .local\Claim_Summary_medical.csv .local\Claim_Summary_dental.csv .local\Claim_Summary_pharmacy.csv `
  --input .local\eob_medical --estimates .local\estimates.csv --output .local\output
# Review .local\output\review_candidates.csv in Excel, approve or fix flagged rows, then:
python cigna_eob_local\cigna_eob_extract.py finalize --review .local\output\review_candidates.csv --output .local\output
```

**3. Check the outputs for leaks.** Optionally list extra private terms (address, member ID, …), one per line, in `.local\leak_terms.txt`; drop `--terms-file` if you don't.

```powershell
python cigna_eob_local\leak_scan.py --outputs .local\output --claims-csv .local\Claim_Summary_medical.csv .local\Claim_Summary_dental.csv .local\Claim_Summary_pharmacy.csv --input .local\eob_medical --terms-file .local\leak_terms.txt
```

**4. Describe your plans.** Copy [plan_parameters.example.json](./plan_parameters/plan_parameters.example.json) to `.local\plan_parameters.json`. Fill in every option from your benefits guide and SBCs, then validate:

```powershell
python plan_parameters\validate_plan_parameters.py .local\plan_parameters.json
```

**5. Compare:**

```powershell
python hsa_cost_model\hsa_cost_model.py --claims .local\output\claims_anonymized.csv --plans .local\plan_parameters.json --output .local\output\hsa_comparison.xlsx
```

Open the workbook. Edit the yellow cells on **Parameters** to try different contributions, tax rates or premiums, and read the **Assumptions** sheet before drawing conclusions.

## Repository layout

| Path | Purpose |
|---|---|
| [cigna_eob_local/](./cigna_eob_local/) | Phase 1 extractor (`cigna_eob_extract.py`), leak scanner (`leak_scan.py`), user guide, tests |
| [cigna_eob_synthetic_test_data/](./cigna_eob_synthetic_test_data/) | Synthetic fixture PDFs and expected CSVs (no real data) |
| [plan_parameters/](./plan_parameters/) | Plan-parameters JSON validator and a fictional example |
| [hsa_cost_model/](./hsa_cost_model/) | Phase 2 claims replay and Excel workbook writer |
| [tools/](./tools/) | `privacy_check.py`, the repository privacy guard used by [.githooks/pre-commit](./.githooks/pre-commit) |
| [PRD.md](./PRD.md) | Requirements, design decisions, limitations |
| [AGENTS.md](./AGENTS.md) | Guidance for AI coding agents working in this repository |

## Limitations

- Cigna only, and only the layouts tested so far. Other templates may need label tweaks; the extractor flags anything it can't reconcile.
- One row per claim, not per service line. Service dates are rounded to the month.
- No OCR; image-only PDFs need local OCR or manual entry.
- See [PRD §14](./PRD.md#14-known-limitations-risks-and-issues) for the full list.

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](./CONTRIBUTING.md) first, especially the rule that **all test data must be synthetic**. Never attach real EOBs, claim exports or workbooks to issues or pull requests. To report a privacy problem, see [SECURITY.md](./SECURITY.md).

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```
