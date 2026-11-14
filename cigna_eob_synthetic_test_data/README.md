# Complete synthetic fixture data for Cigna EOB Local Extractor

This folder contains the synthetic inputs for the extractor's regression tests: four end-to-end PDFs and four unit-test PDF inputs (the unit-test PDFs are built with the `fake_eob` function and arguments in `test_extractor.py`). No real patient data is included.

## Contents

- `end_to_end/input/`: exact retained PDFs `a.pdf`, `b.pdf`, `bad.pdf`, `scan.pdf` used for the end-to-end test. The `scan.pdf` case is a blank/no-text PDF, not a realistic image scan.
- `end_to_end/expected_output/`: the original four CSV files produced by the end-to-end test.
- `unit_test_fixtures/unit_valid/`: PDF built with the unit-test's exact sample data and generated extractor outputs.
- `unit_test_fixtures/unit_invalid/`: PDF with deliberately inconsistent paid + owed amounts and corresponding output.
- `unit_test_fixtures/unit_duplicate/`: two byte-for-byte identical PDFs and the outputs flagged as possible duplicates.

The unit tests live in [cigna_eob_local/tests/test_extractor.py](../cigna_eob_local/tests/test_extractor.py). `test_synthetic_fixtures_reproduce_expected_outputs` re-runs every `input/` folder here and requires byte-identical `expected_output/` CSVs. (The earlier archived copy of the test file was removed so the two copies can't drift apart.)

## End-to-end case inputs

| File | Date | Billed | Discount | Not covered | Insurer paid | Patient owes | Expected |
|---|---|---:|---:|---:|---:|---:|---|
| a.pdf | Nov 9, 2025 | 189.00 | 70.05 | 0.00 | 107.06 | 11.89 | Accepted |
| b.pdf | Jan 10, 2026 | 400.00 | 100.00 | 0.00 | 250.00 | 50.00 | Accepted |
| bad.pdf | Feb 1, 2026 | 300.00 | 50.00 | 0.00 | 100.00 | 40.00 | Rejected: paid + owed (140.00) does not reconcile to allowed (250.00) |
| scan.pdf | n/a | n/a | n/a | n/a | n/a | n/a | Rejected: no selectable text |

All three populated original PDFs also contain the synthetic line `Member: SENSITIVE NAME FOR LOCAL TESTING`.

## Unit-test inputs

| Case | Filename | Date | Billed | Discount | Not covered | Insurer paid | Patient owes | Synthetic person/reference |
|---|---|---|---:|---:|---:|---:|---:|---|
| unit_valid | private_jane_doeeob.pdf | Nov 9, 2025 | 189.00 | 70.05 | 0.00 | 107.06 | 11.89 | TEST PERSON SECRET / 998877665544 |
| unit_invalid | bad.pdf | Jan 10, 2026 | 400.00 | 100.00 | 0.00 | 70.00 | 90.00 | SAMPLE TEST PERSON / 998877665544 |
| unit_duplicate | first.pdf | Jan 10, 2026 | 400.00 | 100.00 | 0.00 | 250.00 | 50.00 | SAMPLE TEST PERSON / 998877665544 |
| unit_duplicate | second.pdf | Jan 10, 2026 | 400.00 | 100.00 | 0.00 | 250.00 | 50.00 | identical bytes to first.pdf |

The test fixtures use a simplified, fictional Cigna-like layout. They are NOT copies of Cigna templates and do not establish compatibility with every real Cigna EOB.

## Re-run tests

From the repository root:

```powershell
python -m pytest cigna_eob_local -q
```

To run the extractor against one group of fixtures (the output folder must not already contain a `review_candidates.csv`, or add `--force`):

```powershell
python cigna_eob_local\cigna_eob_extract.py extract --input cigna_eob_synthetic_test_data\end_to_end\input --output "PATH_TO_OUTPUT_DIRECTORY"
```

Compare the newly created CSVs with the CSVs under `expected_output/`. If an intentional change alters the output, regenerate every `expected_output/` folder and explain why in the commit.

Caution: in the tests, all identifiable-looking strings are synthetic; they are included to verify that the extractor does not leak them into CSVs.
