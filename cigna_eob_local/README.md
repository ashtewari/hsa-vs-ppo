# Cigna claims financial extractor (local-only)

Extract **de-identified financial summaries** from your Cigna claims, for comparing your current plan with an HSA-eligible HDHP. Inputs can be:

- **myCigna "Claim Summary" CSV exports** (medical, dental, pharmacy). This is the recommended claims list.
- **Text-searchable Cigna Explanation of Benefits (EOB) PDFs.** These fill in the allowed / discount / not-covered amounts that the medical CSV lacks. They can also be used on their own.
- An optional **estimates CSV** for spending that has no claim record, such as dependents' prescriptions you can't see or vision costs.

**Privacy:** This script runs on your own computer. It opens your files locally, uses **no AI model, cloud API, network client, analytics or telemetry**, and never writes names, diagnoses, provider names, drug names, member IDs, claim/Rx numbers, descriptions, filenames, or exact dates into the output CSVs. Output columns come from a fixed allow-list. `pip install` may contact the package repository to get the PDF library, but the script itself never transmits your data.

**Limitations:** EOB templates differ. The extractor reads the **claim summary** of each EOB rather than individual service lines, so one claim becomes one row. Image-only scans and unusual or adjusted claim accounting are flagged for review, not guessed. Dental EOB PDFs are not parsed; the dental CSV already holds every amount. `member_owes` is the *estimated responsibility* shown by Cigna, **not proof of what you paid**.

## 1. Install on Windows (PowerShell)

Install Python 3.10 or newer if needed. Put this folder somewhere private, then open PowerShell in it:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If PowerShell blocks activation, skip it and run `.\.venv\Scripts\python.exe` instead of `python` in every example below. If you set up the whole toolkit from the repository root (see the [top-level README](../README.md)), use that environment instead.

## 2. Gather your files

Keep originals in a private folder and choose a **different** output folder:

```text
C:\Users\YourUser\Documents\PrivateClaims\
    Claim_Summary_medical.csv       <- myCigna claims page, download/export as CSV (one per coverage type)
    Claim_Summary_dental.csv
    Claim_Summary_pharmacy.csv
    eob_medical\*.pdf               <- the medical EOB PDFs
    estimates.csv                   <- optional, see section 4
C:\Users\YourUser\Documents\EOB_Analysis_Output\
```

The CSV type (medical / dental / pharmacy) is detected from its header row. If you can't download every EOB, the CSVs alone still work. Medical rows without a matching EOB get an estimated allowed amount and are queued for your review.

## 3. Extract financial data

Recommended (claims CSVs + EOB PDFs + estimates):

```powershell
python cigna_eob_extract.py extract `
  --claims-csv PrivateClaims\Claim_Summary_medical.csv PrivateClaims\Claim_Summary_dental.csv PrivateClaims\Claim_Summary_pharmacy.csv `
  --input PrivateClaims\eob_medical `
  --estimates PrivateClaims\estimates.csv `
  --output EOB_Analysis_Output
```

PDF-only (no CSVs): `python cigna_eob_extract.py extract --input PrivateClaims\eob_medical --output EOB_Analysis_Output`

How the inputs combine:

| Input | Becomes | Amounts |
|---|---|---|
| Medical CSV row with a matching EOB PDF (same service date, billed, and plan paid) | `CLM_xxxxx`, `source=claims_csv+eob_pdf` | Discount and not-covered come from the EOB; the network comes from the EOB's "Provider Network Status" |
| Medical CSV row with no matching PDF | `CLM_xxxxx`, `source=claims_csv`, flagged `no_matching_eob_pdf` | allowed = plan paid + patient responsibility (`derived_from_paid_plus_owed`) |
| Dental CSV row | `CLM_xxxxx`, `coverage_type=dental` | discount = Billed − Contracted; not_covered = Not Allowed + Not Covered By Plan; allowed = Covered Amount |
| Pharmacy CSV row | `CLM_xxxxx`, `coverage_type=pharmacy` | allowed = plan paid + patient responsibility |
| EOB PDF in PDF-only mode | `EOB_xxxxx`, `source=eob_pdf` | Read from the claim summary |
| Estimates row | `EST_xxxxx`, `source=estimate` | As entered |

`member_owes` is the CSV's **Patient Responsibility** (not "I Owe", which subtracts payments already made). Patients are replaced by anonymous `Member_A`, `Member_B`, … tags. Tags are assigned by first name in alphabetical order, so the same person gets the same tag in every file. The tag map, the CSV/PDF → ID map, and unmatched PDF filenames are printed to your **terminal only**. Add `--quiet` to print aggregates only (no filenames, no tag map), for example when an AI assistant reads the terminal for you.

Outputs:

- `review_candidates.csv`: **editable, de-identified** rows with warnings. Local working file; never share it.
- `claims_anonymized.csv`: only rows that passed validation or were approved by you.
- `annual_summary.csv`: totals per year × coverage type, with actual and estimated amounts reported separately (`data_basis`).
- `annual_by_category.csv`: the same totals split by broad service category.

**Re-running `extract` never silently overwrites your edits.** If `review_candidates.csv` already exists, `extract` stops. Use `finalize` to rebuild the reports, or add `--force`, which first copies the old file to `review_candidates.backup-YYYYMMDD-HHMMSS.csv`.

## 4. Estimates for spending with no claim record (optional)

Use estimates for spending you know about but can't export. Examples: adult dependents' pharmacy claims (Cigna hides these from the subscriber), or vision costs. Columns:

```text
member_tag,coverage_type,category,start_month,end_month,every_n_months,unit_allowed,unit_plan_paid,unit_member_owes
Member_B,pharmacy,pharmacy,2023-03,2024-12,1,12.00,4.00,8.00
Member_A,vision,vision,2023-06,2024-06,12,350.00,100.00,250.00
```

Each row repeats every `every_n_months` from `start_month` to `end_month`. `unit_plan_paid + unit_member_owes` must equal `unit_allowed`. Use the `member_tag` printed by `extract`, or leave it blank. **Do not enter drug names or other descriptions.** Estimated rows are approved automatically (`review_status=estimate`) and are always reported separately from actual claims.

## 5. Review the extraction before relying on it

Open `review_candidates.csv` **locally in Excel** and compare against the original EOBs / myCigna. Fields:

- `coverage_type`: `medical`, `dental`, `pharmacy`, or `vision`
- `billed`: provider's submitted charge
- `discount`: negotiated discount ("Cost Reduction"); not an amount you pay
- `not_covered`: charge not covered by the plan; you may or may not owe it
- `allowed_estimated`: negotiated (allowed) amount; `allowed_basis` says how it was obtained
- `plan_paid`: insurer payment
- `member_owes`: estimated patient responsibility, NOT an actual receipt
- `service_month`: month of service only (not a full identifiable date)
- `category`: broad category only. For medical CSV rows it is guessed from the provider's name (lab, imaging, urgent care, …) in memory; otherwise `medical_unspecified`. Correct it if you know better.
- `network`: read from the EOB when printed; otherwise `unknown`. Set `in_network` or `out_of_network` when you verify it.
- `member_tag`: anonymous family identifier, e.g. `Member_A`
- `source`: where the row came from (see the table in section 3)
- `approved`: `yes` means include it in calculations; `no` means exclude it

A row with `approved=no` was not automatically trusted. Read its `issue_codes` (see Troubleshooting) and correct the amounts **using the original local EOB or myCigna**. Set `allowed_basis=manually_verified` after confirming, then `approved=yes`. Every approved row must satisfy both checks:

- `billed − discount − not_covered = allowed_estimated`
- `plan_paid + member_owes = allowed_estimated + not_covered`

If an EOB has balances that won't reconcile (other insurance, reversals, adjustments), keep it unapproved; don't force inaccurate numbers.

**Warning:** Do not edit `eob_id`. Do not paste medical descriptions, provider names, or claim identifiers into the CSV. There is no notes column on purpose.

## 6. Rebuild reports after manual corrections

```powershell
python cigna_eob_extract.py finalize --review EOB_Analysis_Output\review_candidates.csv --output EOB_Analysis_Output
```

If validation rejects an approved row, the command prints only its anonymous ID and a reason. The summaries cover accepted rows only and may be incomplete while rows are still unapproved.

## 7. Check the outputs for leaks, then decide what to share

Before sharing anything, run the local leak scan. It collects sensitive values from your private inputs and searches every output CSV for them. Those values include names, providers, drugs, Rx numbers, exact dates, filenames, claim numbers, and 7+ digit IDs printed on the EOBs (member, account, group). It prints **only counts**. Put anything else you want checked, such as your street address or member ID, one per line in a private text file and pass `--terms-file`:

```powershell
python leak_scan.py --outputs EOB_Analysis_Output --claims-csv PrivateClaims\Claim_Summary_medical.csv PrivateClaims\Claim_Summary_dental.csv PrivateClaims\Claim_Summary_pharmacy.csv --input PrivateClaims\eob_medical --terms-file PrivateClaims\leak_terms.txt
```

Then share, at first, only:

- `annual_summary.csv`
- `annual_by_category.csv`

For a claim-by-claim model you can later share `claims_anonymized.csv` after inspecting it. Removing identifiers reduces sensitivity, but spending patterns and month-level data may still reveal personal medical details. You can drop `member_tag` or `category` first. Never share `review_candidates.csv`, its backups, or the terminal output (it contains filenames and first names). Keep your originals on your computer.

## 8. Notes on correctness

- One row **per claim**, not per service line. Multi-service claims with mixed cost sharing reduce replay fidelity.
- Cigna's EOB omits the "Amount not covered" row when it is zero. The extractor treats a missing row as $0.00 only when no "not covered" label exists in the summary's label column. Both reconciliation checks still apply.
- Allowed amounts under a different network or plan can differ from the current negotiated amounts.
- Corrected or duplicate claims can be hard to reconcile. The script flags identical PDFs, repeated claim numbers, and identical CSV rows.
- Service dates are rounded to **month**, so within-month order is approximate, and deductible timing within a month could change estimates.
- Plan years need not align with calendar years; set the correct plan-year start month for the later model.
- EOBs and claim summaries don't necessarily reflect provider bills, write-offs, HRA/FSA reimbursements, or amounts you actually paid.

## Troubleshooting (issue codes)

| Code | Meaning / what to do |
|---|---|
| `password_protected`, `empty_pdf`, `cannot_open_or_parse_pdf`, `cannot_hash_pdf` | The PDF couldn't be read. Re-download it, or enter the figures by hand. |
| `scanned_or_no_selectable_text` | Image-only PDF. Use a **local** OCR tool if desired, or enter the figures by hand. |
| `multiple_claim_summaries` | More than one claim in one PDF. Split it locally, without double-counting. |
| `ambiguous_<field>` | The same label showed different amounts. Check the EOB. |
| `missing_service_date`, `missing_<amount>` | Not found. Fill in `service_month` (`YYYY-MM`) or the amount from the original. |
| `negative_or_adjusted_claim` | Credit or reversal. Reconcile it with the original claim; don't count it as ordinary. |
| `billed_discount_allowed_inconsistent` | billed − discount − not covered ≠ allowed. Check the amounts. |
| `paid_plus_owed_not_reconciled` | plan paid + member owes ≠ allowed + not covered. Look for adjustments, secondary insurance, or noncovered items. Do not blindly approve. |
| `noncovered_not_in_member_owes` | Cigna's "What I owe" excludes the not-covered amount. If you paid/owe it, add it to `member_owes`. If the provider wrote it off, move it into `discount` and set `not_covered` to 0. |
| `noncovered_amount_needs_review` | A not-covered amount exists. Confirm whether you owe it. |
| `duplicate_pdf`, `possible_reprocessed_claim` | Same file or same claim number twice. Keep only the final claim. |
| `possible_duplicate_claim_row` | Identical rows in the claims CSVs (for example the same file passed twice). Approve only one. |
| `claim_denied`, `claim_not_final` | The claim was denied or is still pending in myCigna. Usually leave it unapproved. |
| `no_matching_eob_pdf` | Medical CSV row without an EOB. Allowed was estimated as plan paid + patient responsibility; confirm it in myCigna (look for any not-covered amount). |
| `ambiguous_pdf_match`, `multiple_pdfs_for_claim` | A PDF matched several claim rows, or several PDFs matched one row. Check by hand. |
| `pdf_csv_member_owes_mismatch` | The EOB's "What I owe" differs from the CSV's Patient Responsibility. Use the current value from myCigna. |

## Commands

```text
python cigna_eob_extract.py extract [--claims-csv CSV ...] [--input PDF_FOLDER] [--estimates CSV] --output CSV_FOLDER [--force] [--quiet]   # at least one input; estimates alone work when there are no claim records
python cigna_eob_extract.py finalize --review REVIEW_CSV --output CSV_FOLDER
python leak_scan.py --outputs CSV_FOLDER [--claims-csv CSV ...] [--input PDF_FOLDER] [--terms-file TXT]
```

The script is provided for local financial analysis, not medical interpretation or insurance claim adjudication.
