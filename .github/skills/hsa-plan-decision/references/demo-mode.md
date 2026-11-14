# Demo mode: rehearse the whole workflow on the fictional demo family

Use this when the person wants to see how the skill works before using their own data ("show me a demo", "walk me through it with the demo family", "try it without my data"). It has the same five phases, gates and [privacy protocol](privacy-protocol.md). Only the data is fictional, and every file lives under `demo/output/` instead of `.local/`. Allow 10-15 minutes.

Tell the person at the start:

- The "Demo" family of four, their providers, claims and plans are made up. Nothing they type needs to be personal. They can accept every pre-filled answer, or change any of them to see what happens.
- The steps are the ones we'd take with their own data, so afterwards they'll know what to fetch.
- Wherever a real person would download something, the file is already there.

## Setup

```
python demo/run_demo.py --inputs-only
```

This writes `demo/output/inputs/` and wipes any earlier demo output:

| File | Stands in for |
|---|---|
| `Claim_Summary_medical.csv`, `Claim_Summary_dental.csv`, `Claim_Summary_pharmacy.csv` | myCigna claim-summary exports for 2025 |
| `eob_medical/` (13 PDFs) | EOB PDFs for 13 of the 15 medical claims |
| `benefits_summary.md` | The employer's Summary of Benefits and Coverage (SBC) and premium sheet for both plans |
| `leak_terms.txt` | The private-terms list the person would write themselves |

Don't create `.local/`, and don't touch it if it exists. Treat the claim files exactly like real PHI: never open the CSVs or PDFs, and always run `extract` with `--quiet`. Showing that the protocol works is part of the demo.

## Phase 1 - Interview with pre-filled answers

Ask the batches in [intake-interview.md](intake-interview.md) as usual. Give the one-line reason for each question; that's what teaches. Offer the demo family's answer as the recommended default, labelled "(demo family)":

| Question | Demo family |
|---|---|
| Who will be covered | Two adults and two children (tier `family`) |
| Current plan and insurer | Example PPO, Cigna |
| Plans to choose from | Example PPO, Example HDHP |
| Enrollment deadline | None (demo) |
| Pay frequency | Every two weeks (26 paychecks) |
| Federal bracket / state rate | 22% / 5% |
| HSA contributions through payroll | Yes |
| HSA contribution | The maximum: the $9,000 family limit minus the $1,500 from the employer = $7,500 |
| HSA-eligible all year / spouse has own HSA / 55 or older | Yes / no / no |
| General-purpose FSA or HRA, Medicare, other coverage, claimed as a dependent | None |
| Children are tax dependents | Yes |
| Records | myCigna medical, dental and pharmacy claim exports for 2025; EOB PDFs for most medical claims |
| Planned care next year | None |

These answers match the `tax`, `coverage_tier` and `pay_periods_per_year` values in `plan_parameters/plan_parameters.example.json`. Write the notes to `demo/output/intake_notes.md`.

If the person changes an answer, carry it through:
- a different tax rate, pay frequency, tier or payroll answer: edit it in `demo/output/plan_parameters.json` in Phase 2;
- a different HSA contribution: pass `--employee-hsa` in Phase 4;
- an ineligibility answer: take the "Stop and say so" path in SKILL.md, which is worth showing.

Tell them their numbers will then differ from the ones in [demo/README.md](../../../../demo/README.md).

## Phase 2 - Plans

1. Explain where real numbers come from (see "Where the numbers come from" in [plan-parameters.md](plan-parameters.md)). In the demo, the plan document is `demo/output/inputs/benefits_summary.md`. Plan documents hold no PHI, so read it.
2. Copy `plan_parameters/plan_parameters.example.json` to `demo/output/plan_parameters.json`. Go through it with the person against the benefits summary: premiums for their tier, employer HSA money, deductibles (embedded vs. aggregate), out-of-pocket maximums, coinsurance, every service rule, and the prescription settings. Point out the traps the example shows: the HDHP's aggregate family deductible, the HDHP's prescription deductible, and the PPO's labs with no deductible. The values match by design. If the person changed Phase 1 answers, edit those values now and record each one under `assumptions`. Set `"current_plan": "example_ppo"`.
3. Validate:

   ```
   python plan_parameters/validate_plan_parameters.py demo/output/plan_parameters.json
   ```

   Expect `VALID`, with only `tax.healthcare_fsa_annual` missing (fine, because there is no FSA). Read the per-plan summary lines to the person.

## Phase 3 - Claims

Follow path A (Cigna) in [claims-data.md](claims-data.md). Explain where each file comes from on myCigna, and that the demo's copies are already in `demo/output/inputs/`.

```
python cigna_eob_local/cigna_eob_extract.py extract --quiet --claims-csv demo/output/inputs/Claim_Summary_medical.csv demo/output/inputs/Claim_Summary_dental.csv demo/output/inputs/Claim_Summary_pharmacy.csv --input demo/output/inputs/eob_medical --output demo/output/extract
```

Expect 43 rows: 41 auto-validated and 2 flagged `no_matching_eob_pdf`. Those are the two medical claims without an EOB; their allowed amount was estimated as plan paid + patient responsibility. Review them with the person. Explain the code, and what they would check on myCigna with real data (whether there's a not-covered amount). In the demo the amounts are right, so offer two ways to approve them:

- **The real way:** open `demo/output/extract/review_candidates.csv` in Excel, filter `approved` = `no`, set `approved` to `yes` and `allowed_basis` to `manually_verified` on both rows, and save as CSV.
- **The shortcut:** you run `python demo/run_demo.py --approve-flagged demo/output/extract/review_candidates.csv`.

Then:

```
python cigna_eob_local/cigna_eob_extract.py finalize --review demo/output/extract/review_candidates.csv --output demo/output/extract
```

Expect 43 accepted and 0 not approved. There are four member tags, `Member_A` to `Member_D`. If the person wants to see the tag map, they run `extract` without `--quiet` in their own terminal, as they would with real data.

For the leak scan, explain that with real data they write `leak_terms.txt` themselves; the demo's copy is pre-written.

```
python cigna_eob_local/leak_scan.py --outputs demo/output/extract --claims-csv demo/output/inputs/Claim_Summary_medical.csv demo/output/inputs/Claim_Summary_dental.csv demo/output/inputs/Claim_Summary_pharmacy.csv --input demo/output/inputs/eob_medical --terms-file demo/output/inputs/leak_terms.txt
```

Gate: `PASS`. After it passes, the de-identified outputs are fine to read together (see the "Fine" section of the privacy protocol).

## Phase 4 - Run the comparison

```
python hsa_cost_model/hsa_cost_model.py --claims demo/output/extract/claims_anonymized.csv --plans demo/output/plan_parameters.json --output demo/output/hsa_comparison_demo.xlsx
```

With the default answers: Example PPO $7,785.58, Example HDHP $2,995.43, and baseline 2025 modeled $1,329.00 vs. actual $1,329.00 (100.0%) PASS. Explain what the baseline proves. Different net costs are expected if the person changed answers. A failed baseline is not expected; diagnose it as in [results-and-recommendation.md](results-and-recommendation.md).

## Phase 5 - Explain the result

Follow [results-and-recommendation.md](results-and-recommendation.md) with the workbook open. In the default demo:

- The HDHP is about $4,790 cheaper in a typical year. The family pays $2,707 more at the doctor and pharmacy, but premiums are $5,200 lower before tax, the employer adds $1,500, and contributing $7,500 saves $2,599 in tax.
- The HDHP also wins in the high-use and worst-case scenarios, so there is no break-even point. The person's own plans may well have one.
- Their HSA contribution is savings, not a cost; Cash flow and Projection show where it goes.

Then invite one what-if. For example, re-run Phase 4 with `--employee-hsa 0` (the HDHP is still cheaper here, by less), or change a premium on the **Parameters** sheet and watch the chart. Use the same no-advice wording as a real run, and say that with their own plans and claims the answer can flip.

## Wrap up

List what they would gather for their own run: the benefits summary or SBC and premium sheet for each option, a pay stub, their tax bracket, claim exports for each coverage type, and EOB PDFs (optional). Their files go in `.local/`. Ask whether they want to start with their own data. If so, begin at "Before you start" in SKILL.md. They can delete `demo/output/` at any time.
