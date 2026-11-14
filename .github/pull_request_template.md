## Summary

<!-- What does this change and why? -->

## Privacy checklist

- [ ] No real EOBs, claim exports, outputs, workbooks, screenshots, names, IDs, providers, drugs or exact dates anywhere in this PR (code, tests, fixtures, docs, commit messages, description).
- [ ] All new test data is synthetic.
- [ ] The pre-commit privacy guard ran (`git config core.hooksPath .githooks`) and passes: `python tools/privacy_check.py --all`.
- [ ] No new runtime network access, telemetry or cloud/AI calls.
- [ ] New output fields or text-handling code paths have a test asserting synthetic secrets don't reach any CSV.

## Tests

- [ ] `python -m pytest -q` passes.
- [ ] If expected fixture output changed on purpose, every `expected_output/` folder was regenerated and the reason is explained above.
