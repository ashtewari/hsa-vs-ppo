# AGENTS.md

Guidance for AI coding agents working in this repository.

## Project

HSA vs. PPO decision toolkit built on the user's own Cigna Explanation of Benefits (EOB) PDFs. See [PRD.md](./PRD.md) for full requirements and status.

- **Phase 1 (implemented):** offline CLI that turns myCigna claim CSVs and EOB PDFs into de-identified financial CSVs, plus a local leak scanner.
- **Phase 2 (implemented, v1):** [hsa_cost_model/](./hsa_cost_model/) replays the de-identified claims against each plan's rules (PRD §8), driven by a validated plan-parameters JSON file, and writes an Excel workbook. Phase 2 may use `openpyxl` (its own `requirements.txt`); Phase 1 stays standard library + PyMuPDF.

## Layout

| Path | Purpose |
|---|---|
| [cigna_eob_local/cigna_eob_extract.py](./cigna_eob_local/cigna_eob_extract.py) | The extractor (single-file CLI: `extract`, `finalize`). Handles PDF-only mode, claim-CSV import (EX-28) with PDF matching, and estimates. |
| [cigna_eob_local/leak_scan.py](./cigna_eob_local/leak_scan.py) | Local PHI leak scan of output CSVs against the private inputs; prints counts only. |
| [cigna_eob_local/tests/](./cigna_eob_local/tests/) | `unittest` regression tests that generate synthetic PDFs/CSVs on the fly and check every synthetic fixture byte-for-byte. |
| [cigna_eob_local/README.md](./cigna_eob_local/README.md) | End-user guide, including the issue-code table. |
| [cigna_eob_synthetic_test_data/](./cigna_eob_synthetic_test_data/) | Synthetic fixture PDFs and expected CSV outputs. |
| [plan_parameters/](./plan_parameters/) | Plan-parameters validator, fictional example JSON, and tests. Real plan files live in `.local/`. |
| [hsa_cost_model/](./hsa_cost_model/) | Phase 2 claims replay + Excel workbook writer (`hsa_cost_model.py`) and hand-calculated tests. |
| [demo/](./demo/) | One-command synthetic demo (`run_demo.py`: fictional family → every Phase 1/2 step → workbook), its data generator (claims, EOBs, and a benefits summary rendered from the example plan parameters), the Windows+Excel screenshot script, and an end-to-end test that pins the demo's net costs. The skill's demo mode ([references/demo-mode.md](./.github/skills/hsa-plan-decision/references/demo-mode.md)) uses `run_demo.py --inputs-only` and `--approve-flagged` and quotes the pinned figures; keep them in sync. |
| [docs/images/](./docs/images/) | `demo-*.png` screenshots of the synthetic demo: the only images the privacy guard allows. Regenerate with `demo/make_screenshot.py` when the pinned demo numbers or the Annual comparison sheet change. |
| [tools/privacy_check.py](./tools/privacy_check.py) | Repository privacy guard used by the pre-commit hook in [.githooks/](./.githooks/): blocks private file types and `.local/`, scans for PII patterns and private terms. |
| [tools/profile_table.py](./tools/profile_table.py), [tools/convert_claims.py](./tools/convert_claims.py) | Data intake for non-Cigna or unfamiliar files: describe a CSV/XLSX without printing values; map any claims export onto the extractor's claim-summary layouts (or write a blank manual-entry template). |
| [.github/skills/hsa-plan-decision/](./.github/skills/hsa-plan-decision/) | Agent skill that walks a non-expert through the whole analysis with their own data. **When helping someone run the analysis, load and follow it.** Keep it in sync when CLI options, layouts, or issue codes change. |
| [.claude/skills/hsa-plan-decision/](./.claude/skills/hsa-plan-decision/), [CLAUDE.md](./CLAUDE.md) | Claude Code entry points. The skill is a stub that points to the canonical `.github/skills/` copy; its frontmatter must match the canonical `name`/`description` exactly (`tools/tests/test_agent_skills.py` checks this). `CLAUDE.md` imports this file. Edit the canonical skill and this file, not the stubs. |
| `.local/` | **Gitignored.** The user's real claims, EOBs, plan documents, estimates, plan parameters, and outputs. Never commit, print, or paste its contents. |

## Environment

Use the repository's virtual environment for every Python command (scripts, tests, `pip install`, one-off snippets); don't use the system/base Python. Each shell call in an agent session starts fresh and activation does not persist, so **call the venv's interpreter directly**:

```powershell
.\.venv\Scripts\python.exe <script-or-args>            # Windows
.venv/bin/python <script-or-args>                       # macOS / Linux
```

If the venv is missing, create it from the repository root:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

If the user has set up a different environment (for example a conda env, called with `conda run -n <env> python ...`), use that one consistently instead. The commands below write `python` for whichever interpreter applies.

Dependencies are listed in `requirements.txt` (runtime) and `requirements-dev.txt` (+ pytest). Phase 1 is limited to the standard library plus PyMuPDF (PRD NF-6); do not add dependencies without a clear need.

## Commands

Run from the repository root.

```powershell
# All tests, including byte-for-byte checks of every synthetic fixture's expected CSVs
python -m pytest -q

# Privacy guard over every tracked file (the pre-commit hook runs it on staged files)
python tools\privacy_check.py --all

# Validate a plan-parameters file
python plan_parameters\validate_plan_parameters.py plan_parameters\plan_parameters.example.json

# End-to-end demo on a fictional family (writes demo\output\, gitignored); screenshot needs Windows + Excel
python demo\run_demo.py
python demo\make_screenshot.py

# Phase 2 workbook from the user's real (local) data
python hsa_cost_model\hsa_cost_model.py --claims .local\output\claims_anonymized.csv --plans .local\plan_parameters.json --output .local\output\hsa_comparison.xlsx
```

If a change intentionally alters output, regenerate every `cigna_eob_synthetic_test_data/**/expected_output/` folder (run `extract` into a temp folder and copy the CSVs) and explain why in the commit.

### Working with the user's real data (`.local/`)

Only run real data through local commands. Report **aggregates only** (counts, issue-code tallies, pass/fail, de-identified annual totals) back into the conversation. Never print raw PDF/CSV text, names, providers, drugs, IDs, or exact dates. Filter the extractor's terminal output (it prints the member-tag map with first names, and filenames). The real outputs go to `.local\output`; `extract` refuses to overwrite an existing review file without `--force`.

## Privacy rules (non-negotiable)

This project handles protected health information. Every change must preserve these rules (PRD §7.7, §10, §11):

- **No network access at runtime.** No HTTP clients, AI/cloud APIs, analytics, or telemetry.
- **Allow-list outputs.** CSVs are built from explicit field lists (`FIELDS`, `ACCEPTED_FIELDS`, `SUMMARY_FIELDS`, `CATEGORY_FIELDS`). Never write names, member/claim/reference/Rx IDs, provider or drug names, diagnoses, descriptions, filenames, raw PDF/CSV text, or exact dates (only `YYYY-MM`).
- **Terminal only.** The filename → ID map, PDF → claim matches, and member-tag map are printed, never persisted.
- **No exception text in CSVs.** Error details that may contain paths go to stderr only.
- **Never commit real EOBs or real output.** `.gitignore` blocks `*.pdf`/`*.csv` outside the synthetic fixtures; do not override it, and do not bypass the pre-commit privacy guard (`--no-verify`). Test data must be synthetic.
- Any new output field or code path that touches PDF text needs a test asserting synthetic secrets do not appear in any CSV (see `test_good_claim_and_no_pii_exports`).

## Coding conventions

- Money: `decimal.Decimal` quantized to cents (`CENT`); never `float`. Reconciliation tolerance is $0.01.
- Deterministic output: stable ordering and fixed precision, so the same inputs give byte-identical CSVs.
- Conservative by default: rows that fail validation stay `approved=no` with an issue code; never silently include doubtful figures.
- CSVs are written as UTF-8 with BOM (`utf-8-sig`) so they open cleanly in Excel.
- Windows first, but no Windows-only APIs; use `pathlib`.
- When adding or changing an issue code, add it to `ISSUE_CODES` (tests fail on unlisted codes) and update the README issue-code table and the PRD status tables.
