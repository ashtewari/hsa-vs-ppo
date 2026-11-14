# Phase 3 - Claims history

Goal: `.local/output/claims_anonymized.csv` covering 1-3 recent years for everyone on the plan, built from whatever the person has. Commands below are run from the repository root (Windows: use `\` paths and `.\.venv\Scripts\python.exe` if `python` isn't the venv).

## Step 1 - Inventory

From the interview, list each source per person and year: insurer export, EOB PDFs, paper, nothing. Ask the person to put files in `.local/raw/` (any names). Never open them yourself.

## Step 2 - Per source

### A. Cigna (myCigna "Claim Summary" CSV exports, Cigna EOB PDFs)
Use directly. Medical, dental and pharmacy CSVs are recognised by header; medical EOB PDFs fill allowed/discount/not-covered amounts. See `cigna_eob_local/README.md`.

**Watch the mix:** whenever any `--claims-csv` is given, PDFs only *supplement* matching medical CSV rows; PDFs without a matching row are reported and **not imported**. If medical history exists only as Cigna PDFs while other coverage is in CSVs, run two extractions into separate folders (`--input` only into `.local/output_pdf`, CSVs into `.local/output`) and pass both `claims_anonymized.csv` files to the cost model (`--claims A B`). PDF-only rows have a blank `member_tag`: during review the person fills it in with the same tag letters the CSV run printed, otherwise everyone in the PDF run is treated as one person.

### B. Any other claims export (CSV or XLSX)
1. Profile it (prints headers, kinds and counts, never values):
   ```
   python tools/profile_table.py .local/raw/export.xlsx [--sheet NAME] [--skip-rows N]
   ```
   If the header looks wrong, there are title rows above the table: add `--skip-rows`. For a categorical column (claim type, status, network) you may run `--show-values "Claim Type"`; the tool refuses name/provider/drug columns.
2. Propose a mapping in words and confirm it with the person. Target layouts:

   | Layout (`--kind`) | Columns (required unless marked optional) |
   |---|---|
   | `medical` | Service Date, Patient, Provider (optional), Status, Billed, Plan Paid, Patient Responsibility |
   | `dental` | Service Date, Patient, Provider (optional), Status, Billed, Not Allowed, Covered Amount, Contracted Amount, Plan Paid, Not Covered By Plan, Patient Responsibility |
   | `pharmacy` | Fill Date, Rx Number (optional), Drug Name (optional), For, Amount Billed, My Cigna Plan Paid, Patient Responsibility |

   Meaning: **Billed** = provider's charge; **Plan Paid** = what insurance paid; **Patient Responsibility** = what the member owed after insurance (deductible + copay + coinsurance), *not* "amount you still owe after payments". Typical source names: "Your cost", "Member responsibility", "You owe", "Patient share", "Amount you may owe". If the export has an allowed/negotiated column but no plan-paid column, Plan Paid = Allowed − Patient Responsibility; do that in a small local script, or ask the person to add the column in Excel.
3. Convert (one run per layout; `--where` splits a combined export). Keep the command on **one line** (PowerShell doesn't use `\` for line continuation; use a backtick there if you must wrap):
   ```
   python tools/convert_claims.py --kind medical --input .local/raw/export.xlsx --output .local/claims_medical.csv --where "Claim Type=Medical" --map "Service Date=Date of Service" --map "Patient=Member" --map "Billed=Billed Amount" --map "Plan Paid=Plan Paid" --map "Patient Responsibility=Your Cost" --set "Status=Processed"
   ```
   - Status: map it if present (Processed/Paid/Finalized pass; Denied and anything else get flagged). Otherwise `--set "Status=Processed"` only for exports that list finalized claims.
   - Dates: day-first files need `--date-format %d/%m/%Y`; the tool refuses ambiguous columns.
   - Patient column with member IDs or relationship codes instead of names: `--patient-ids-to-names` for IDs. If the tool warns that patients collapse to one first word, or the distinct-patient count is lower than the number of people, fix it before continuing (family members would merge).
   - Dental exports without "Not Allowed" / "Not Covered By Plan": `--set "Not Allowed=0" --set "Not Covered By Plan=0"`; Contracted Amount and Covered Amount usually both equal the allowed amount.
   - Read back the printed counts (rows written, months covered, distinct patients) and compare with what the person expects.

### C. Other insurers' EOB PDFs, paper statements, a personal spreadsheet
The PDF parser only knows Cigna layouts. Options, best first:
1. Ask whether the insurer's site has a claims export (most do; sometimes under "Claims" > "Download" or "Statements"). Use path B.
2. Manual entry: `python tools/convert_claims.py --kind medical --template --output .local/manual_medical.csv`, then the person types one row per claim in Excel (Billed, Plan Paid, Patient Responsibility from each EOB). Offer to explain each EOB field; don't ask them to send you the EOB.
3. Too many claims to type: yearly totals as estimates (section D, "annual totals").

### D. Nothing, gaps, or future plans -> `estimates.csv`
Columns: `member_tag,coverage_type,category,start_month,end_month,every_n_months,unit_allowed,unit_plan_paid,unit_member_owes`.
- Each row repeats every `every_n_months` from `start_month` to `end_month` (`YYYY-MM`). `unit_plan_paid + unit_member_owes` must equal `unit_allowed`.
- `unit_member_owes` = what the **current plan** would charge for one occurrence (its copay, or deductible/coinsurance). The model uses it to recognise the service, so copays must be exact.
- `member_tag`: tags come from the claim files (`Member_A`, ... in alphabetical order of first names). Order matters: (1) run `extract` once on the claim files only, before any review; (2) the person runs it without `--quiet` in their own terminal, or reads the tags off their screen, and tells you which tag is "you", "spouse", "child 1" (never names); (3) write the estimates; (4) re-run `extract --force` with `--estimates`, and only then start the review. With **no** claim files, assign tags yourself (`Member_A` = you, `Member_B` = spouse, ...) and record that in `.local/intake_notes.md`.
- **Planned or new care next year** (surgery, pregnancy, a new monthly prescription): the model's scenarios use the latest complete historical year as the base year. Add the new care as estimate rows **dated inside that base year** in a separate estimates file and compare two runs (with and without it), or, for a single big event, use the cost model's `--high-event <allowed amount>` and read the High scenario. Say which approach you used.

| Care | coverage_type | category | unit_member_owes (current plan) |
|---|---|---|---|
| Primary-care or specialist visit | medical | office_visit | that visit's copay |
| Preventive visit / screening | medical | preventive | 0 |
| Urgent care / ER visit | medical | urgent_care / emergency | that copay |
| Lab work, X-ray | medical | laboratory | coinsurance x allowed (or deductible) |
| MRI / CT | medical | imaging | deductible then coinsurance |
| Therapy session (PT, mental health) | medical | therapy | copay or coinsurance |
| Outpatient surgery / hospital stay / childbirth | medical | outpatient_procedure / inpatient | deductible + coinsurance up to the OOP max |
| Recurring prescription (one fill) | pharmacy | pharmacy | the tier copay (0 for ACA-preventive drugs) |
| Dental, vision | dental / vision | dental / vision | member cost under those plans (same for every medical option) |

Allowed amounts: best is the insurer's cost-estimator tool or a recent EOB for the same service. Otherwise use these **rough US ballparks** and label them: preventive visit 250, primary-care visit 150, specialist 250, urgent care 200, ER visit 2,500, basic lab panel 80, X-ray 150, MRI 1,200, therapy session 125, outpatient surgery 6,000, hospital stay 15,000, childbirth 13,000, generic 30-day Rx 15, preferred brand 30-day Rx 300.

**Annual totals only** (e.g. "we had about 9,000 of allowed charges and paid about 1,200"): split into a few rows per year by the main categories, `every_n_months` 12, and tell the person this loses deductible timing.

**Hidden dependents' claims:** estimate them, then calibrate against the family out-of-pocket accumulator on the member site or recent EOBs: family total minus the visible members' total is roughly the hidden spending.

## Step 3 - Extract, review, finalize

```
python cigna_eob_local/cigna_eob_extract.py extract --quiet --claims-csv <all claim CSVs> [--input <Cigna EOB folder>] [--estimates .local/estimates.csv] --output .local/output
```
- Always use `--quiet` (aggregates only). The tag map is for the person's eyes; see the `member_tag` steps above.
- Summarise: rows per coverage type, auto-validated vs needs review, issue-code counts (count them from `review_candidates.csv`, which holds no names).
- Review with the person, one issue code at a time (meanings in `cigna_eob_local/README.md`). They open `review_candidates.csv` in Excel and decide; you explain options. Converted exports without EOBs are all flagged `no_matching_eob_pdf`: once the person confirms the export's patient-responsibility totals look right, a small local script may set `approved=yes` for rows whose *only* code is that one.
- `python cigna_eob_local/cigna_eob_extract.py finalize --review .local/output/review_candidates.csv --output .local/output`
- Re-running `extract` needs `--force` and overwrites review edits (a backup is made). Save review decisions in a small script under `.local/` so they can be re-applied.

## Step 4 - Completeness check (recommended)

Compare per year: sum of `member_owes` for actual medical + pharmacy rows (from `claims_anonymized.csv`) vs the insurer's in-network out-of-pocket accumulator (member site, or running totals printed on recent EOBs). Explain any gap: hidden dependents, claims after the last statement, a partial first year, out-of-network accumulators kept separately. Large unexplained gaps mean missing claims; go back to Step 2.

## Step 5 - Leak scan

Follow the "Before sharing results" steps in `privacy-protocol.md`. Require `PASS`.
