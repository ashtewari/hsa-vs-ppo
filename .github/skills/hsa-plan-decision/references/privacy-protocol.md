# Privacy protocol (follow for the whole session)

You may be a cloud-hosted model. Treat the person's claims as protected health information (PHI): names, member IDs, providers, drugs, diagnoses, exact dates, claim numbers, addresses. The toolkit is designed so you never need to see any of it.

## Never

- Open, `cat`, `type`, `Get-Content`, view, or paste claim CSVs, EOB PDFs, statements, or screenshots of them. Don't ask the person to paste rows into the chat or upload images of claims.
- Print raw cell values in commands you write. If you need to understand a file, use `tools/profile_table.py` (headers, kinds and counts only; headers and sheet names that look like data are hidden). If headers are hidden, ask the person to read the column names to you from Excel, or to confirm your proposed mapping there.
- Use frequency-based masking ("show words that appear in most files") to peek at documents: the person's own name and address appear in every file and will leak. If you ever must look at layout, use an explicit allow-list of known template words, and prefer not to.
- Copy the extractor's terminal output verbatim: by default it prints filenames and the member-tag map (first names). **Always run `extract` with `--quiet`** (aggregates only). When the person needs the tag map, ask them to run the same command without `--quiet` in their own terminal and tell you only which tag is "you", "spouse", "child 1".
- Allow personal names in file or folder names: ask the person to save files under `.local/` with generic names (`claims_medical.csv`, `eob_medical/`), because error messages include paths.
- Commit anything under `.local/`, or any `.csv`, `.xlsx`, `.pdf` outside the synthetic fixtures. The pre-commit hook blocks this; never bypass it.

## Fine

- Plan documents (SBC, benefits guide, premium sheet, rate sheet) usually contain no PHI. You may read them to fill in the plan parameters. Ask first if one might include the person's name or ID (enrollment confirmations often do).
- Counts, issue-code tallies, date ranges as `YYYY-MM`, column names, and the de-identified outputs: `annual_summary.csv`, `annual_by_category.csv`, `claims_anonymized.csv` (after the leak scan passes).
- Aggregates the tools print: rows written, months covered, distinct patients, auto-validated counts, baseline accuracy, net costs.

## Before sharing results

1. Ask the person to list private terms (street, city, member ID, last name, employer, children's names) one per line in `.local/leak_terms.txt`. They write it, not you.
2. Run `python cigna_eob_local/leak_scan.py --outputs .local/output --claims-csv <their claim CSVs> --input <their EOB folder> --terms-file .local/leak_terms.txt`. Require `PASS`.
3. Only the Tier 1 files (`annual_summary.csv`, `annual_by_category.csv`) are suitable to share beyond their computer; `claims_anonymized.csv` only after they inspect it. Never `review_candidates.csv`.

## If something leaks into the conversation

Tell the person immediately and plainly what was exposed, stop the step that caused it, tighten the method, and continue. Don't repeat the leaked value.
