# PRD: HSA vs. PPO Decision Toolkit (Cigna EOB–based)

| | |
|---|---|
| **Status** | v1.0 |
| **Phase 1 (EOB extractor)** | ✅ Implemented: claim-CSV + EOB PDF extraction, review/finalize workflow, local leak scan |
| **Phase 2 (cost comparison model)** | ✅ v1 implemented: claims replay + Excel workbook with a baseline accuracy check |

---

## 1. Summary

The user wants to decide whether to switch from their current health plan (a traditional PPO-style plan through Cigna) to an HSA-eligible High Deductible Health Plan (HDHP) at the next open enrollment. The decision should rest on the user's **own claims history**, not on generic premium/deductible comparisons.

The toolkit has two parts:

1. **Local EOB Extractor (Phase 1).** An offline Python CLI turns myCigna claim-summary CSV exports and Cigna Explanation of Benefits (EOB) PDFs into **de-identified** financial CSVs. Original medical documents never leave the user's computer.
2. **Cost Comparison Model (Phase 2).** It replays the de-identified claims history, in chronological order, against both plans' cost-sharing rules. It then adds premiums, employer HSA contributions, and tax effects, and reports net annual cost, break-even, worst-case exposure, cash flow, and a multi-year projection.

## 2. Problem statement

- Comparing plans on premium and deductible alone is misleading. What a person pays depends on when each claim happens, the negotiated (allowed) amount, copays vs. coinsurance, and the out-of-pocket (OOP) maximum.
- The information needed for an accurate comparison sits in years of Cigna EOB PDFs. These contain sensitive PHI: names, member IDs, diagnoses, providers, and claim numbers.
- Uploading unredacted EOBs to a cloud AI service carries privacy risk.
- Doing this by hand across 2–3 years of claims takes a long time and invites mistakes: duplicate or reprocessed claims, reversals, and figures that don't reconcile.

## 3. Goals and non-goals

### Goals

| ID | Goal |
|---|---|
| G1 | Answer: *"Would HDHP + HSA leave me financially better off, and how much additional healthcare spending would it take to reverse that advantage?"* |
| G2 | Use 2–3 years of the user's actual Cigna claims (for example the two prior years plus the current year to date) as the basis. |
| G3 | Keep original EOBs and all PHI on the user's machine. Only de-identified financial aggregates may be shared externally. |
| G4 | Be conservative: never silently include doubtful figures. Flag them for human review. |
| G5 | Compare **total annual financial cost** (premiums + OOP − employer HSA funding − incremental tax savings), not just point-of-care costs. |
| G6 | Show risk as well as the expected outcome: low, typical, and high-utilization scenarios plus worst-case exposure. |

### Non-goals

- Medical interpretation, diagnosis coding, or insurance claim adjudication.
- Proof of what was actually paid. EOB "member owes" is an estimate, not a receipt.
- Support for payers other than Cigna, or for every Cigna EOB template, in Phase 1.
- OCR of image-only PDFs. Users may use a **local** OCR tool or enter figures manually.
- Tax filing advice or HSA investment advice.
- Any network transmission, telemetry, or AI/cloud processing inside the tooling.

## 4. Users

| Persona | Description |
|---|---|
| **Primary: the employee deciding at open enrollment** | Technically comfortable Windows user who can run Python from PowerShell. Has access to myCigna, the employer benefits guide, and the SBCs. May cover family members. |
| **Secondary: an analyst/assistant (human or AI)** | Receives **only** de-identified outputs (annual summaries, optionally the anonymized claim list) plus plan parameters, and builds or interprets the comparison. |

## 5. Guiding principles

1. **Privacy by architecture.** PHI is processed locally. Outputs are built from an allow-list of fields, not by redacting raw text.
2. **Conservative by default.** If a row fails a reconciliation check, it is excluded from totals until a human approves it.
3. **Human in the loop.** The user reviews and corrects the extracted figures against the original PDFs.
4. **Transparent limitations.** Every summary states that it covers accepted claims only and may be incomplete.
5. **Replay, don't extrapolate.** Estimate HDHP costs by applying the new plan's rules to each historical claim, not by scaling past spend by a factor.

## 6. End-to-end user journey

| Step | Actor | Action | Output |
|---|---|---|---|
| 1 | User | Gather 2–3 years of claims from myCigna: medical EOB PDFs, pharmacy claims, annual deductible/OOP summaries. Ask Cigna for a consolidated itemized claims history if available. | Private folder of EOB PDFs |
| 2 | User | Collect plan documents for **every** option: the upcoming plan year's benefits guide, SBCs, per-paycheck premiums, employer HSA contribution. | Plan parameters (see §9) |
| 3 | Extractor | `extract` reads the claim CSVs and PDFs locally, matches EOBs to claims, adds estimates, and writes de-identified CSVs and a review queue. | `review_candidates.csv` and 3 report CSVs |
| 4 | User | Reviews `review_candidates.csv` in Excel against the original PDFs / myCigna, corrects amounts, `network`, and `category`, and approves rows. | Edited review CSV |
| 5 | Extractor | `finalize` re-validates approved rows and rebuilds the reports. `leak_scan.py` confirms no input PHI appears in any output. | Final de-identified CSVs |
| 6 | User | Shares **only** `annual_summary.csv` and `annual_by_category.csv` at first, and later optionally `claims_anonymized.csv`, plus plan parameters. | Shareable data set |
| 7 | Model (Phase 2) | Replays claims under both plans, applies premiums, HSA funding, and taxes, and runs the scenarios. | Comparison report / workbook |
| 8 | User | Decides on a plan and an HSA contribution level. | Enrollment decision |

---

## 7. Phase 1: Local EOB Extractor (functional requirements)

Implementation: [cigna_eob_extract.py](./cigna_eob_local/cigna_eob_extract.py) and [leak_scan.py](./cigna_eob_local/leak_scan.py). User guide: [README.md](./cigna_eob_local/README.md).

Status legend: ✅ Implemented · 🟡 Partial · ⬜ Planned

### 7.1 Platform and installation

| ID | Requirement | Status |
|---|---|---|
| EX-1 | Runs on Windows from PowerShell with Python ≥ 3.10. | ✅ |
| EX-2 | Single third-party dependency: `pymupdf>=1.24,<2` ([requirements.txt](./cigna_eob_local/requirements.txt)). Fails with an install hint if missing. | ✅ |
| EX-3 | Works from a venv or by calling `.\.venv\Scripts\python.exe` directly when script activation is blocked. | ✅ (documented) |

### 7.2 CLI

| ID | Requirement | Status |
|---|---|---|
| EX-10 | `extract --output <csv_dir>` with `--input <pdf_dir>` and/or `--claims-csv <csv> ...`: builds the review queue, then runs `finalize` automatically. | ✅ |
| EX-11 | `finalize --review <review_candidates.csv> --output <csv_dir>`: re-validates the edited review file and rebuilds the reports. | ✅ |
| EX-12 | `--input` must be an existing directory and `--claims-csv` / `--estimates` existing files. The output directory is created if absent. | ✅ |
| EX-13 | Files are processed in case-insensitive path order and given sequential anonymous IDs: `EOB_00001…` (PDF-only mode), `CLM_00001…` (claim CSV rows), `EST_00001…` (estimates). | ✅ |
| EX-14 | The filename → ID map, PDF → claim match, and member-tag map are printed to the **terminal only** and never written to CSV. | ✅ |
| EX-15 | Exit code 0 on success, 1 on handled errors (`ValueError`, `OSError`, `csv.Error`). | ✅ |
| EX-16 | `--estimates <csv>`: user-entered recurring spending with no claim record (e.g. dependents' Rx, vision), expanded into monthly rows with `source=estimate`. | ✅ |
| EX-17 | `--force`: required to overwrite an existing `review_candidates.csv`; makes a timestamped backup first (RV-8). | ✅ |

### 7.3 Extraction

| ID | Requirement | Status |
|---|---|---|
| EX-20 | Read the **claim summary page**: the first page containing "Summary of a claim for services on" (real EOBs may start with an "Explanation of payment" cover page), else page 1. Produce one row per claim. | ✅ |
| EX-21 | Extract six amounts by label matching (case-insensitive). Labels must start their line and sit in the same column as the "Amount billed" label. Labels that wrap onto a second line ("Amount not / covered") are joined. The value is taken from the same row or, if the next line is purely numeric, from that line: | ✅ |
| | • `billed`: "Amount billed", "Charges submitted", "Total billed" | |
| | • `discount`: "Discount", "Network savings", "Cost Reduction" | |
| | • `not_covered`: "Amount not covered", "Not covered amount". Cigna omits this row when it is zero; it is inferred as $0.00 only if no "not covered" text exists in the label column | |
| | • `allowed`: "Covered amount", "Allowed amount", "Amount allowed", "Negotiated amount" | |
| | • `plan_paid`: "What Cigna/your plan paid", "What Cigna/insurance paid", "Plan paid" | |
| | • `member_owes`: "What I/you owe", "You may owe", "Patient responsibility" | |
| EX-22 | Parse money values: `$`, thousands separators, negatives as `-x` or `(x)`. Use `Decimal` arithmetic rounded to cents. | ✅ |
| EX-23 | If `allowed` is not printed, derive it as `billed − discount − not_covered` and set `allowed_basis = derived_from_billed_discount_notcovered`. Otherwise `direct_eob`. | ✅ |
| EX-24 | Service date comes **only** from lines labelled as service dates ("Summary of a claim for services on", "Date(s) of service", "Service date"), never from received/processed dates. Output is reduced to `YYYY-MM`. | ✅ |
| EX-25 | Category is assigned from a fixed allow-list using unambiguous phrases. If zero or several categories match, use `medical_unspecified`. For medical claim-CSV rows the provider's name is matched in memory against keywords (lab, imaging, urgent care, emergency, therapy). Dental and pharmacy rows use `dental` / `pharmacy`. Raw descriptions and provider names are never output. | ✅ (real EOBs carry no service descriptions or procedure codes) |
| EX-26 | `network` is read from the EOB's "Provider Network Status" when printed, otherwise `unknown`. `member_tag` is assigned automatically from the claim CSV's patient (first name, case-insensitive, alphabetical → `Member_A`, `Member_B`, …); blank in PDF-only mode. | ✅ |
| EX-27 | Read pharmacy claims (✅ via the myCigna pharmacy CSV) and per-line items of multi-service EOBs (⬜ deferred). | 🟡 |
| EX-28 | Accept myCigna "Claim Summary" CSV exports (medical, dental, pharmacy; detected by header). Medical rows are matched in memory to EOB PDFs on exact service date + billed + plan paid to fill in discount / not covered / allowed. Without a match, allowed = plan paid + patient responsibility, and the row is flagged. `member_owes` = Patient Responsibility. Dental: discount = Billed − Contracted, not_covered = Not Allowed + Not Covered By Plan, allowed = Covered Amount. | ✅ |
| EX-29 | Each row carries `coverage_type` (`medical`, `dental`, `pharmacy`, `vision`) and `source` (`eob_pdf`, `claims_csv`, `claims_csv+eob_pdf`, `estimate`). | ✅ |

Allowed categories: `medical_unspecified`, `office_visit`, `preventive`, `laboratory`, `imaging`, `urgent_care`, `emergency`, `inpatient`, `outpatient_procedure`, `therapy`, `pharmacy`, `dental`, `vision`, `other`.

### 7.4 Validation and issue codes

A row is auto-approved (`approved=yes`, `review_status=auto_validated`) **only if no issue codes are raised**.

| ID | Rule | Issue code |
|---|---|---|
| VA-1 | PDF is encrypted | `password_protected` |
| VA-2 | PDF has zero pages | `empty_pdf` |
| VA-3 | First page has < 80 characters of selectable text | `scanned_or_no_selectable_text` |
| VA-4 | PDF cannot be opened or parsed (no exception text is written) | `cannot_open_or_parse_pdf` |
| VA-5 | More than one "Summary of a claim for services on" in the PDF | `multiple_claim_summaries` |
| VA-6 | The same label yields conflicting amounts | `ambiguous_<field>` |
| VA-7 | No service date found | `missing_service_date` |
| VA-8 | Required amount missing | `missing_billed`, `missing_plan_paid`, `missing_member_owes`, `missing_allowed`, `missing_not_covered`, `missing_discount` |
| VA-9 | Any amount is negative (credit/reversal) | `negative_or_adjusted_claim` |
| VA-10 | `|billed − discount − not_covered − allowed| > $0.01` | `billed_discount_allowed_inconsistent` |
| VA-11 | `|plan_paid + member_owes − allowed − not_covered| > $0.01`. If instead `plan_paid + member_owes = allowed` (Cigna's "What I owe" excludes the not-covered amount), the more specific code is used | `paid_plus_owed_not_reconciled` / `noncovered_not_in_member_owes` |
| VA-12 | `not_covered ≠ 0` | `noncovered_amount_needs_review` |
| VA-13 | SHA-256 of the file matches an earlier PDF | `duplicate_pdf` |
| VA-14 | Same claim/reference number found in more than one PDF (all such rows flagged) | `possible_reprocessed_claim` |
| VA-15 | File cannot be read for hashing | `cannot_hash_pdf` |
| VA-16 | Claim CSV status is Denied / not final | `claim_denied` / `claim_not_final` |
| VA-17 | Identical rows in the claim CSVs (same member, date, provider or Rx, amounts) | `possible_duplicate_claim_row` |
| VA-18 | Medical CSV row has no matching EOB PDF (allowed derived from paid + owed) | `no_matching_eob_pdf` |
| VA-19 | One PDF matches several CSV rows / several PDFs match one row | `ambiguous_pdf_match` / `multiple_pdfs_for_claim` |
| VA-20 | EOB "What I owe" differs from the CSV's Patient Responsibility | `pdf_csv_member_owes_mismatch` |

All codes are listed in `ISSUE_CODES` in the extractor; tests fail if an unlisted code is emitted.

### 7.5 Review and finalize

| ID | Requirement | Status |
|---|---|---|
| RV-1 | `review_candidates.csv` can be edited in Excel. It has **no free-text notes column** on purpose, so PHI cannot be pasted in. | ✅ |
| RV-2 | The user may correct amounts, `service_month`, `category`, `network`, and `member_tag`, then set `allowed_basis=manually_verified` and `approved=yes`. | ✅ |
| RV-3 | `finalize` re-checks every approved row: `approved` ∈ {yes, y, true, 1}; ID matches `(EOB|CLM|EST)_\d{5,}`; `service_month` matches `20YY-MM`; `coverage_type`, `category`, `source` are in their allow-lists; `network` ∈ {unknown, in_network, out_of_network}; `member_tag` is blank or `Member_[A-Za-z0-9]{1,12}`; all six amounts present and non-negative; both reconciliation identities hold within $0.01; `allowed_basis` ∈ {direct_eob, derived_from_billed_discount_notcovered, derived_from_paid_plus_owed, direct_claims_csv, estimated, manually_verified}. | ✅ |
| RV-4 | Repeated IDs are rejected (`duplicate_eob_id`). | ✅ |
| RV-5 | Rejected approved rows are reported on the terminal by anonymous ID and reason only. | ✅ |
| RV-6 | A row keeps `review_status=auto_validated` only if it was auto-validated and has no issue codes. Estimates keep `estimate`. Otherwise it becomes `manually_approved`. | ✅ |
| RV-7 | Accepted rows are sorted by `(service_month, id)` and given `sequence_in_month` (001, 002, …). Order within a month is approximate. | ✅ |
| RV-8 | Protect manual edits: `extract` refuses to overwrite an existing `review_candidates.csv`; with `--force` it first copies it to `review_candidates.backup-YYYYMMDD-HHMMSS.csv`. | ✅ |

### 7.6 Output files

All CSVs are UTF-8 with BOM (Excel-friendly).

| File | Contents | Shareable? |
|---|---|---|
| `review_candidates.csv` | Every claim row: `approved, eob_id, service_month, coverage_type, category, member_tag, network, billed, discount, not_covered, allowed_estimated, allowed_basis, plan_paid, member_owes, source, review_status, issue_codes` | Local working file only |
| `claims_anonymized.csv` | Accepted rows: `eob_id, service_month, sequence_in_month, coverage_type, category, member_tag, network, billed, discount, not_covered, allowed_estimated, allowed_basis, plan_paid, member_owes, source, review_status` | Tier 2, after inspection |
| `annual_summary.csv` | Per year × coverage type × data basis (`actual` / `estimate`): `year, coverage_type, data_basis, accepted_claims, total_billed, estimated_allowed, insurance_paid, member_responsibility, not_covered` | Tier 1 |
| `annual_by_category.csv` | Per year × coverage type × category × data basis: `year, coverage_type, category, data_basis, accepted_claims, estimated_allowed, member_responsibility` | Tier 1 |

### 7.7 Privacy requirements (Phase 1)

| ID | Requirement | Status |
|---|---|---|
| PR-1 | No network client, AI model, cloud API, analytics, or telemetry in the tool. `pip install` is the only network activity. | ✅ |
| PR-2 | CSVs never contain names, addresses, member IDs, claim/reference/Rx numbers, provider or drug names, diagnoses, descriptions, filenames, or exact dates. | ✅ (tested with synthetic name, address, DOB, provider, drug, Rx #, reference #, filename, full date across PDF, CSV and estimate inputs) |
| PR-3 | Raw PDF text and coordinates stay in memory and are never persisted. | ✅ |
| PR-4 | Exception messages are never written to CSV. Filesystem errors (which may contain paths) go to local stderr only. | ✅ |
| PR-5 | Docs tell users to keep originals and outputs in **separate private folders**, and to remove employee identifiers from plan documents before sharing. | ✅ |
| PR-6 | Docs warn that month-level spending patterns can still reveal sensitive information. Users may drop `member_tag` or `category` before sharing. | ✅ |
| PR-7 | `leak_scan.py` searches every output CSV for sensitive values collected from the private inputs and prints counts only. It covers names, providers, drugs, Rx numbers, exact dates, filenames, claim numbers, 7+ digit IDs printed on the EOBs, and an optional terms file. | ✅ |

---

## 8. Phase 2: HSA vs. PPO Cost Comparison Model (requirements)

### 8.1 Core cost model

For each plan and each modeled year:

```text
Net annual cost = Employee premiums
                + Modeled medical & pharmacy out-of-pocket
                − Employer HSA contribution
                − Incremental tax savings (HSA vs. whatever the PPO already offers, e.g. FSA)
```

Employee HSA contributions are **not** an expense. The money stays the user's and only the tax effect is counted.

### 8.2 Functional requirements

| ID | Requirement | Priority |
|---|---|---|
| CM-1 | **Inputs:** `claims_anonymized.csv` (preferred) or the annual summaries, plus a plan-parameter file for each plan (§9). | P0 |
| CM-2 | **Claims replay:** process claims in `(service_month, sequence_in_month)` order within each plan year. For each claim, apply in turn: preventive exemption → copay (if the plan uses one for that category) → deductible accumulation → coinsurance → OOP-max cap. Use `allowed_estimated` as the cost basis. | P0 |
| CM-3 | **Plan year** may differ from the calendar year. A configurable start month rebuckets claims. | P0 |
| CM-4 | **Family coverage:** use `member_tag` to model individual vs. family deductibles/OOP max, **embedded vs. aggregate**. | P0 |
| CM-5 | **Network:** apply in-network rules to `in_network` rows. Flag `unknown`/`out_of_network` rows and report results with and without them. | P1 |
| CM-6 | **Baseline check:** replaying the current plan should come close to historical `member_responsibility`. Report the difference as a model-accuracy indicator. | P0 |
| CM-7 | **Premiums:** per-paycheck amount × pay periods. Handle the pre-tax (Section 125) vs. post-tax flag. | P0 |
| CM-8 | **Employer HSA contribution:** annual amount and timing (lump sum vs. per paycheck). | P0 |
| CM-9 | **Tax savings:** employee HSA contribution × (federal + state marginal rate, + FICA if made via payroll). Subtract any tax benefit the PPO already gives (healthcare FSA) to avoid double-counting. | P0 |
| CM-10 | **Contribution limits:** employer + employee contributions must stay within the annual IRS limit for the coverage tier, plus the 55+ catch-up if eligible. Warn if exceeded. | P0 |
| CM-11 | **HSA eligibility checks:** warn if a general-purpose healthcare FSA or other disqualifying coverage is present, or if the HDHP deductible/OOP is outside IRS bounds. | P1 |
| CM-12 | **Scenarios:** *Low* (preventive + occasional visits + recurring Rx), *Typical* (historical replay with adjustments for known recurring care), *High/worst case* (adds major events until each plan reaches its OOP max). | P0 |
| CM-13 | **Break-even:** the extra allowed spend at which the HDHP's net cost equals the PPO's. | P0 |
| CM-14 | **Worst-case exposure:** maximum annual cost under each plan (premiums + OOP max − HSA employer funding − tax savings). | P0 |
| CM-15 | **Cash flow:** month-by-month cumulative OOP vs. cumulative HSA balance. Flag months where the HSA balance would not cover bills (early-year deductible risk). | P1 |
| CM-16 | **3–5-year projection:** cumulative savings and HSA balance with configurable contribution, investment return, and medical-cost inflation assumptions. | P2 |
| CM-17 | **Sensitivity:** vary key assumptions (utilization ±, allowed-amount changes, network changes) and show the effect on the result. | P2 |
| CM-18 | **Output:** a workbook or report with tabs for historical spending by year, claims replay detail, premium savings, employer HSA funding, tax savings, break-even, worst-case, cash flow, and projection. All plan parameters are editable. | P0 |
| CM-19 | Every output is labelled as an **estimate**. List the assumptions that drive the result. | P0 |

**Implementation status:** [hsa_cost_model.py](./hsa_cost_model/hsa_cost_model.py) writes the workbook; see its [README](./hsa_cost_model/README.md).

| Requirement | Status | Notes |
|---|---|---|
| CM-1, CM-2, CM-3, CM-4 | ✅ | Untyped claims get their service from the current plan's actual copay / coinsurance / deductible pattern; Rx tier from the copay actually paid |
| CM-5 | 🟡 | Out-of-network claims use each plan's out-of-network rules with separate accumulators; a "without out-of-network" variant is not reported separately |
| CM-6 | ✅ | Default tolerance 10% (`--tolerance`); a partial first year is excluded from pass/fail |
| CM-7 – CM-11 | ✅ | Pre-tax premiums save the combined marginal rate; your HSA contribution defaults to the IRS maximum minus employer money (editable); eligibility checks are in the plan-parameters validator |
| CM-12 – CM-15 | ✅ | Low = pharmacy + preventive claims of the base year; high = typical + one $20,000 inpatient event; worst case = in-network OOP maximum; break-even adds one event of growing size |
| CM-16 | ✅ | Strategy: invest any HSA balance; HSA-eligible spending (incl. dental and vision) is paid from the HSA |
| CM-17 | 🟡 | Utilization scaling ×0.5 – ×2.0; allowed-amount and network sensitivity not separate |
| CM-18, CM-19 | ✅ | Parameter-driven sheets are live Excel formulas whose results equal the Python engine; the claim replay is values |

### 8.3 Illustrative output (hypothetical figures)

| Annual amount | PPO | HDHP + HSA |
|---|---:|---:|
| Premiums | $6,000 | $3,600 |
| Medical out-of-pocket | $900 | $2,800 |
| Employer HSA contribution | $0 | −$800 |
| **Net cost before tax adjustments** | **$6,900** | **$5,600** |

The HDHP saves $1,300/yr before taxes in this example. The model must show the same layout with the user's real data, then add tax effects, scenarios, and break-even.

---

## 9. Data inputs required from the user

### 9.1 Claims history (from myCigna)

- Medical, dental, and pharmacy **myCigna Claim Summary CSV exports** for all covered members, covering 2–3 years.
- Medical EOB PDFs, used to fill in allowed / discount / not-covered amounts the medical CSV lacks.
- Annual deductible and OOP summaries, used to check the extracted totals.
- **Estimates** (`--estimates`) for spending with no exportable claim record, for example adult dependents' pharmacy claims (hidden from the subscriber) or vision under a separate plan.

### 9.2 Plan parameters (for **every** option, from the benefits guide and SBCs)

Captured in a plan-parameters JSON file and checked by [validate_plan_parameters.py](./plan_parameters/validate_plan_parameters.py). Null values are reported as missing, and IRS HDHP rules are checked. [plan_parameters.example.json](./plan_parameters/plan_parameters.example.json) is a fictional template. Each user keeps their real file in the gitignored `.local/` folder. It should record each value's source document and list every interpretation as an explicit assumption.

| Parameter | Current PPO | Proposed HDHP |
|---|---|---|
| Coverage tier (self / self+1 / family) | Required | Required |
| Employee premium per paycheck; pay periods/yr; pre-tax? | Required | Required |
| Plan-year start month | Required | Required |
| Deductible (individual / family; embedded or aggregate) | Required | Required |
| OOP maximum (individual / family) | Required | Required |
| PCP / specialist / urgent care / ER copay or coinsurance | Required | Required |
| Lab, imaging, outpatient, inpatient cost sharing | Required | Required |
| Preventive care coverage | Required | Required |
| Prescription drug tiers and cost sharing | Required | Required |
| Network differences | Required | Required |
| Employer HSA contribution (amount, timing) | Usually $0 | Required |
| Existing healthcare FSA contribution | If any | Must be $0 (general-purpose) |

### 9.3 Tax inputs

- Federal and state marginal income-tax rates.
- Whether HSA contributions go through payroll (FICA savings).
- Age 55+ (catch-up eligibility).

### 9.4 Regulatory reference values (example figures; **always verify against the IRS for your plan year**)

| HSA rule (example year) | Self-only | Family |
|---|---:|---:|
| Max annual HSA contribution (employer + employee) | $4,500 | $9,000 |
| Min HDHP deductible | $1,750 | $3,500 |
| Max HDHP out-of-pocket | $8,700 | $17,400 |
| Catch-up (age 55+) | +$1,000 | +$1,000 |

These values must be configuration, not hard-coded constants (they live in `irs_limits` in the plan-parameters file).

---

## 10. Data-sharing policy

| Tier | Data | Guidance |
|---|---|---|
| **Never share** | Original EOB PDFs and claim-summary CSV exports, `review_candidates.csv` and its backups, `estimates.csv`, terminal output (contains filenames and first names), names, DOB, addresses, member/claim/policy IDs, provider names, diagnosis/treatment text | Keep local |
| **Tier 1 (share first)** | `annual_summary.csv`, `annual_by_category.csv`, plan parameters with employee identifiers removed | Low sensitivity |
| **Tier 2 (share if needed)** | `claims_anonymized.csv` after inspection. Optionally drop `member_tag`/`category`. | Moderate: month-level patterns can reveal health events |
| **Conditional** | Prescription names | Only if needed for an accurate Rx comparison |

If a cloud AI assistant is used for Phase 2 analysis, turn off model training in its data controls, consider a temporary chat, redact documents with a real redaction tool (not black boxes drawn on top), and delete uploaded files afterwards.

---

## 11. Non-functional requirements

| ID | Requirement |
|---|---|
| NF-1 | **Offline:** no runtime network access in Phase 1 or Phase 2 tooling. |
| NF-2 | **Deterministic:** the same inputs give byte-identical CSVs (stable ordering, fixed-precision `Decimal`). |
| NF-3 | **Accuracy:** money arithmetic in `Decimal`. Reconciliation tolerance is $0.01. |
| NF-4 | **Performance:** processes a few hundred EOB PDFs in well under a minute on a typical laptop. |
| NF-5 | **Usability:** outputs open cleanly in Excel. Issue codes are documented with fixes in the README troubleshooting section. |
| NF-6 | **Minimal dependencies:** standard library + PyMuPDF only for Phase 1. |
| NF-7 | **Portability:** Windows first, with no Windows-only APIs. |

---

## 12. Testing and acceptance criteria

### 12.1 Existing synthetic test assets

- Unit tests: [test_extractor.py](./cigna_eob_local/tests/test_extractor.py), [test_validate_plan_parameters.py](./plan_parameters/tests/test_validate_plan_parameters.py), [test_cost_model.py](./hsa_cost_model/tests/test_cost_model.py) and [test_privacy_check.py](./tools/tests/test_privacy_check.py). Run from the repository root with `python -m pytest -q`. They cover:
  - every synthetic fixture reproducing its expected CSVs byte-for-byte
  - the real Cigna layout: cover page, wrapped labels, omitted not-covered row, network status
  - claim-CSV import with PDF matching, estimates, RV-8, hash-only duplicates, image-only scans
  - PHI-leak checks and the leak scanner
- Fixtures and expected outputs: [cigna_eob_synthetic_test_data/](./cigna_eob_synthetic_test_data/). Fictional, simplified "Cigna-like" layouts, **not** real Cigna templates.

| Case | Billed | Discount | Not cov. | Plan paid | Owes | Expected |
|---|---:|---:|---:|---:|---:|---|
| E2E `a.pdf` (2025-11) | 189.00 | 70.05 | 0.00 | 107.06 | 11.89 | Accepted, allowed 118.95 |
| E2E `b.pdf` (2026-01) | 400.00 | 100.00 | 0.00 | 250.00 | 50.00 | Accepted, allowed 300.00 |
| E2E `bad.pdf` (2026-02) | 300.00 | 50.00 | 0.00 | 100.00 | 40.00 | Rejected: `paid_plus_owed_not_reconciled` |
| E2E `scan.pdf` | — | — | — | — | — | Rejected: `scanned_or_no_selectable_text` + missing fields |
| Unit valid | 189.00 | 70.05 | 0.00 | 107.06 | 11.89 | Accepted; no PII in any CSV |
| Unit invalid | 400.00 | 100.00 | 0.00 | 70.00 | 90.00 | Excluded: `paid_plus_owed_not_reconciled` |
| Unit duplicate (×2 identical) | 400.00 | 100.00 | 0.00 | 250.00 | 50.00 | Both excluded: `possible_reprocessed_claim` (+ `duplicate_pdf` on 2nd) |

E2E totals: 2 accepted claims, $418.95 estimated allowed, $61.89 member responsibility.

### 12.2 Phase 1 acceptance criteria

Criterion 1 is automated. Criteria 2–4 are checks each user runs on their own data before relying on the results.

| # | Criterion | How |
|---|---|---|
| 1 | All synthetic tests pass and the E2E run reproduces the expected CSVs exactly. | Automated in the test suite |
| 2 | A **real-EOB pilot**: the user confirms that every auto-approved row matches the PDF / myCigna to the cent. | Spot-check auto-approved rows; review every flagged row and record how it was resolved |
| 3 | Accepted annual totals, plus manually reconciled rows, agree with the myCigna annual deductible/OOP summaries, or the gap is explained. | Compare with the deductible/OOP running totals printed on the EOBs. Gaps often come from claims the subscriber can't see (e.g. adult dependents' pharmacy fills); cover them with calibrated estimates |
| 4 | A search of all output CSVs for the user's name, member ID, provider names, and claim numbers finds no matches. | `leak_scan.py` on the final outputs, with a private terms file (address, member ID, …); expect 0 matches in every class |

### 12.3 Phase 2 acceptance criteria

| # | Criterion | How |
|---|---|---|
| 1 | Replaying the current plan reproduces historical member responsibility to within an agreed tolerance per year (default: within 10%). | The workbook's Baseline check sheet; a partial first year is excluded |
| 2 | Hand-calculated test cases pass for: deductible crossing mid-claim, OOP-max cap, preventive exemption, embedded family deductible, and aggregate family deductible. | ✅ [test_cost_model.py](./hsa_cost_model/tests/test_cost_model.py) |
| 3 | The report covers every item in CM-12 to CM-15 and CM-18 to CM-19. | ✅ Workbook sheets: Scenarios, Break-even, Cash flow, Projection, plus Parameters / Assumptions |

---

## 13. Success metrics

| Metric | Target |
|---|---|
| PHI leakage in shareable outputs | 0 occurrences |
| Real EOBs auto-validated without edits | ≥ 70% (stretch: ≥ 90%) |
| Auto-validated rows that turn out wrong on review | 0 |
| Time from PDFs to final Tier 1 summary | < 2 hours for 3 years of claims |
| Decision made before the open-enrollment deadline | Yes |

---

## 14. Known limitations, risks, and issues

### 14.1 Product limitations

- One row **per claim**, not per service line. Multi-service claims with mixed cost sharing reduce replay fidelity.
- Only the claim-summary page is parsed. PDFs with several claim summaries are flagged, not split. Dental EOB PDFs (a per-line table) are not parsed; the dental CSV provides every amount.
- No OCR. Image-only scans need local OCR or manual entry.
- Reversals, adjustments, secondary insurance, and HRA/FSA reimbursements are not modeled. These rows need manual reconciliation.
- `member_owes` is Cigna's estimate, not proof of payment.
- Service dates are rounded to a month, so the order of deductible accumulation within a month is approximate.
- Allowed amounts may differ under a different plan or network.
- Real EOBs carry no service descriptions or procedure codes, so most medical rows stay `medical_unspecified` unless the provider's name reveals the type (lab, imaging, urgent care).
- Adult dependents' pharmacy claims are not visible to the subscriber; they are estimated.
- Pharmacy network status is not in the CSV export (`unknown`).

### 14.2 Code observations found while preparing this PRD

| # | Observation | Resolution |
|---|---|---|
| K1 | The `allowed` label pattern `\bcovered amount\b` also matched "Not covered amount". | ✅ Labels must start their line (and sit in the label column), so "Not covered amount" can't be read as `allowed`. Tested. |
| K2 | `issue_codes` could start with `;` (e.g. `;possible_reprocessed_claim`). | ✅ Empty codes are filtered; expected CSVs regenerated. |
| K3 | No explicit test for SHA-256 `duplicate_pdf`. | ✅ Test with identical PDFs that have no reference number. |
| K4 | `scan.pdf` is a blank page, not an image scan. | ✅ Test with an image-only PDF. |
| K5 | PII-leak test covered one fixture and four strings. | ✅ Leak assertions cover PDF, claim-CSV, and estimate inputs (name, address, DOB, provider, drug, Rx #, reference #, filename, exact date); plus `leak_scan.py` for real outputs. |
| K6 | `test_extractor.py` existed in two places. | ✅ Archived copy and stale results removed; fixtures are checked by the single test suite. |
| K7 | Category heuristics put every fixture in `medical_unspecified`. | 🟡 Real EOBs carry no descriptions or procedure codes. Provider-name keywords categorize some medical rows; the rest need manual categories or Phase 2 inference (from the current plan's copay amounts). |
| K8 | PyMuPDF not installed in the default global Python. | ✅ Docs use a virtual environment with `requirements.txt`; the extractor fails with an install hint if PyMuPDF is missing. |

Real-layout findings (from piloting on real EOBs):
- Cigna EOBs may start with an "Explanation of payment" cover page.
- Labels wrap onto two lines.
- The discount label is "Cost Reduction".
- The not-covered row is omitted when it is zero.
- "What I owe" excludes not-covered amounts.
- In the claim CSV, "I Owe" is net of payments already made, so Patient Responsibility is used.
- Medical CSVs give "First Last" while dental and pharmacy give the first name only.

### 14.3 Risks

| Risk | Mitigation |
|---|---|
| Real Cigna layouts differ from the fixtures, so few rows auto-validate | Real-EOB pilot first. Tune label patterns. Keep conservative rejection. |
| Wrong network or plan rules give a misleading recommendation | Report with/without unknown-network rows. Show assumptions and sensitivity. |
| User approves rows without checking them | README warnings. Reconciliation identities enforced again at `finalize`. |
| Spending patterns re-identify the user | Tiered sharing. Option to drop `member_tag`/`category`. |
| IRS/plan values change year to year | All limits and plan values live in config, with the source/date recorded. |

---

## 15. Design decisions and per-user questions

Decisions already made in the toolkit:

1. **Phase 2 form factor:** a Python script that writes an Excel workbook with live formulas for parameter-driven figures.
2. **Primary claims list:** myCigna claim-summary CSV exports; EOB PDFs fill in the amounts the medical CSV lacks.
3. **HSA strategy in the projection:** invest any HSA balance; HSA-eligible spending is paid from the HSA and the rest compounds.

Questions each user answers through the plan-parameters file and CLI options:

1. **Coverage tier and family deductibles:** which tier, and whether each plan's deductible / OOP max is embedded or aggregate (with any per-person cap).
2. **Plan year:** the start month of the deductible year.
3. **Pharmacy:** whether every member's pharmacy claims are exportable, or some must be estimated and calibrated against EOB running totals.
4. **Existing FSA:** whether a general-purpose healthcare FSA is in use (it blocks HSA eligibility).
5. **Replay tolerance:** how closely the baseline replay must match history (default 10%).
6. **Tax inputs:** federal and state marginal rates, FICA via payroll, 55+ catch-up eligibility.

---

## 16. Milestones

| # | Milestone | Exit criteria |
|---|---|---|
| M0 | Prototype extractor + synthetic tests | ✅ Done |
| M1 | Real-EOB pilot and fixes for K1–K6 | ✅ Done |
| M2 | Claim-CSV import, estimates, review workflow, leak scan | ✅ Done |
| M3 | Plan-parameters schema, validator and fictional example | ✅ Done |
| M4 | Cost model v1 (replay, premiums, HSA funding, tax, scenarios) | ✅ Done (§12.3 #2) |
| M5 | Full report (break-even, worst case, cash flow, projection) | ✅ Done (§12.3 #3) |

---

## 17. Glossary

| Term | Meaning |
|---|---|
| **EOB** | Explanation of Benefits: the insurer's statement of how a claim was processed. Not a bill. |
| **Allowed / negotiated amount** | What the plan recognizes for a service after network discounts. Under an unmet HDHP deductible, this is roughly what the member pays. |
| **HDHP** | High Deductible Health Plan that meets IRS minimum-deductible and maximum-OOP rules, which makes the member HSA-eligible. |
| **HSA** | Health Savings Account: tax-advantaged, rolls over, owned by the employee. It is not insurance. |
| **SBC** | Summary of Benefits and Coverage: a standardized plan summary. |
| **OOP max** | Out-of-pocket maximum: the annual cap on member cost sharing for covered in-network care. |
| **Embedded vs. aggregate deductible** | Embedded: each family member has their own deductible inside the family deductible. Aggregate: the whole family deductible must be met first. |
| **Claims replay** | Applying a plan's cost-sharing rules to historical claims in date order to estimate what the member would have paid. |
