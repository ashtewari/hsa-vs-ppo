# Security and privacy policy

This toolkit handles protected health information on users' own computers. We treat any way it could leak that information as a security issue. Examples:

- the extractor writing a name, ID, provider, drug, filename, exact date or raw text into an output CSV;
- the leak scanner or privacy guard missing a class of sensitive value;
- any network access at runtime;
- personal data committed to this repository.

## Reporting

**Please don't open a public issue.** Report privately through GitHub: on the repository's **Security** tab, choose **Report a vulnerability** ([private vulnerability reporting](https://docs.github.com/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)).

In the report, **never include real health data**: no real EOBs, claim exports, outputs, screenshots, names or IDs. Describe the problem, and if possible reproduce it with made-up values (for example with `fake_eob` in `cigna_eob_local/tests/test_extractor.py`).

If you find personal data committed to the repository, report it the same way so it can be removed from history.

## Supported versions

Only the latest commit on `main` is supported. This is a volunteer project with no response-time guarantee, but privacy reports get priority.
