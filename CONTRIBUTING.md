# Contributing

Thanks for helping. This project processes protected health information (PHI) on people's own computers, so privacy rules come before everything else.

## Privacy rules (non-negotiable)

- **Synthetic data only.** Never commit, attach, paste or screenshot real EOBs, claim exports, `review_candidates.csv`, outputs, workbooks or terminal output, even "redacted". If you need to show a layout problem, rebuild it with made-up values (see `fake_eob` in [test_extractor.py](./cigna_eob_local/tests/test_extractor.py)).
- **No network access at runtime.** No HTTP clients, cloud or AI APIs, analytics or telemetry.
- **Allow-list outputs.** CSVs are built from explicit field lists. Never write names, member/claim/reference/Rx IDs, provider or drug names, diagnoses, descriptions, filenames, raw PDF/CSV text or exact dates (only `YYYY-MM`).
- **No exception text in CSVs.** Errors that may contain paths go to stderr only.
- Any new output field or code path that touches PDF/CSV text needs a test asserting synthetic secrets don't appear in any CSV (see `test_good_claim_and_no_pii_exports`).

[AGENTS.md](./AGENTS.md) has the same rules plus coding conventions (Decimal money, deterministic output, conservative validation).

## Setup

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
git config core.hooksPath .githooks
```

The last line enables the **pre-commit privacy guard** ([tools/privacy_check.py](./tools/privacy_check.py)). On every commit it:

- blocks files under `.local/` and any PDF, CSV, spreadsheet, document, image or archive outside `cigna_eob_synthetic_test_data/`;
- scans staged text for email addresses, phone numbers, SSNs, 7+ digit IDs and absolute user-home paths;
- scans for your own private terms, one per line, in the gitignored `.local/private_terms.txt` and `.local/leak_terms.txt`. Put your name, family names, employer, plan names, providers and member IDs there.

It prints only the file, line and kind of finding, never the value. Don't bypass it with `--no-verify`. If it flags a synthetic test value, add the value to the allow-lists in `privacy_check.py`, or as a last resort add a `privacy-check: ignore` comment on that line. To check the whole tree: `python tools\privacy_check.py --all`.

## Tests

```powershell
python -m pytest -q
```

The suite includes byte-for-byte checks of every synthetic fixture. If a change intentionally alters the extractor's output, regenerate every `cigna_eob_synthetic_test_data/**/expected_output/` folder (run `extract` into a temp folder and copy the CSVs), and explain why in the pull request.

## Pull requests

- Keep changes focused, add tests for new behavior, and update the README issue-code table and PRD status tables when you add or change an issue code (`ISSUE_CODES`).
- Phase 1 stays standard library + PyMuPDF; Phase 2 may use `openpyxl`. Discuss new dependencies first.
- Fill in the privacy checklist in the pull-request template.
