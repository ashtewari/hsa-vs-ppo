#!/usr/bin/env python3
"""Repository privacy guard. Blocks commits that could leak real health or personal data.

Checks each file (staged content by default, or every tracked and untracked-not-ignored file with --all):
  * path rules: nothing under .local/, and no PDF/CSV/spreadsheet/document/image/archive files outside
    the synthetic fixture folder (except docs/images/demo-*.png screenshots of the synthetic demo);
  * text patterns: email addresses, phone numbers, SSNs, 7+ digit IDs, absolute user-home paths;
  * private terms: one term per line in local, gitignored files (default .local/private_terms.txt and
    .local/leak_terms.txt), matched case-insensitively as whole words in file names and contents.
Prints the file, line and finding class only, never the matched value. Exit code 0 = clean, 1 = findings.
Lines containing "privacy-check: ignore" are skipped (for synthetic test values only).
Standard library only, so the pre-commit hook works with any Python 3.10+.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

FIXTURE_DIR = "cigna_eob_synthetic_test_data/"
FIXTURE_EXTENSIONS = {".pdf", ".csv"}
# Screenshots of the synthetic demo (demo/make_screenshot.py) only; never screenshots of real data.
DEMO_IMAGE_RE = re.compile(r"docs/images/demo-[a-z0-9-]+\.png")
PRIVATE_DIRS = (".local/",)
BLOCKED_EXTENSIONS = {
    ".pdf", ".csv", ".tsv", ".xlsx", ".xlsm", ".xls", ".ods", ".doc", ".docx", ".odt", ".rtf",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".webp", ".zip", ".7z", ".rar", ".msg", ".eml",
}
BINARY_EXTENSIONS = BLOCKED_EXTENSIONS - {".csv", ".tsv", ".rtf", ".eml"}
DEFAULT_TERM_FILES = (".local/private_terms.txt", ".local/leak_terms.txt")
# A line containing this marker is not scanned. Use it only for synthetic test values; it is visible in review.
IGNORE_MARKER = "privacy-check: ignore"

# Synthetic values the tests and fixtures use on purpose.
ALLOWED_IDS = {"998877665544", "7654321"}
ALLOWED_EMAIL_DOMAINS = ("users.noreply.github.com", "example.com", "example.org", "example.net")
ALLOWED_HOME_DIRS = {"youruser", "user", "username", "you", "me"}

PATTERNS = {
    "email": re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)"),
    "phone": re.compile(r"(?<![\d.])(?:\+?1[-. ])?\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}(?![\d.])"),
    "ssn": re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"),
    "long_id": re.compile(r"\b[A-Z]{0,2}(\d{7,})\b"),
    "user_path": re.compile(r"(?i)(?:\b[a-z]:[\\/]+users[\\/]+|/home/|/users/)([^\\/\s\"'`<>|]+)"),  # privacy-check: ignore
}


def git(*args: str, root: Path) -> bytes:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, check=True).stdout


def list_files(root: Path, all_files: bool) -> list[str]:
    if all_files:
        out = git("ls-files", "-z", "--cached", "--others", "--exclude-standard", root=root)
    else:
        out = git("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR", root=root)
    return sorted({p for p in out.decode("utf-8").split("\0") if p})


def path_findings(path: str) -> list[str]:
    low = path.replace("\\", "/").lower()
    if any(low.startswith(d) or f"/{d}" in low for d in PRIVATE_DIRS):
        return ["private_folder"]
    suffix = Path(low).suffix
    if suffix in BLOCKED_EXTENSIONS and not (low.startswith(FIXTURE_DIR) and suffix in FIXTURE_EXTENSIONS
                                             or DEMO_IMAGE_RE.fullmatch(low)):
        return [f"blocked_file_type({suffix})"]
    return []


def load_terms(root: Path, term_files: list[Path]) -> list[str]:
    terms: set[str] = set()
    for f in term_files:
        f = f if f.is_absolute() else root / f
        if f.is_file():
            terms.update(t.strip() for t in f.read_text(encoding="utf-8-sig").splitlines()
                         if t.strip() and not t.lstrip().startswith("#"))
    return sorted(terms, key=str.casefold)


def term_patterns(terms: list[str]) -> list[tuple[int, re.Pattern[str]]]:
    return [(i, re.compile(r"(?<![^\W_])" + re.escape(t) + r"(?![^\W_])", re.IGNORECASE))
            for i, t in enumerate(terms, start=1)]


def text_findings(text: str, terms: list[tuple[int, re.Pattern[str]]]) -> list[tuple[int, str]]:
    found = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if IGNORE_MARKER in line:
            continue
        for cls, rx in PATTERNS.items():
            for m in rx.finditer(line):
                if cls == "email" and m.group(1).lower().endswith(ALLOWED_EMAIL_DOMAINS):
                    continue
                if cls == "long_id" and m.group(1) in ALLOWED_IDS:
                    continue
                if cls == "user_path" and m.group(1).lower() in ALLOWED_HOME_DIRS:
                    continue
                found.append((lineno, cls))
        found.extend((lineno, f"private_term#{i}") for i, rx in terms if rx.search(line))
    return found


def check(root: Path, files: list[str], staged: bool, terms: list[str]) -> list[str]:
    patterns = term_patterns(terms)
    report = []
    for path in files:
        blocked = path_findings(path)
        report.extend(f"{path}: {cls}" for cls in blocked)
        report.extend(f"{path}: private_term#{i}_in_path" for i, rx in patterns if rx.search(path))
        if blocked:
            continue
        if Path(path).suffix.lower() in BINARY_EXTENSIONS:
            continue  # allowed binary fixtures (synthetic PDFs) are not text-scanned
        try:
            data = git("show", f":{path}", root=root) if staged else (root / path).read_bytes()
        except (OSError, subprocess.CalledProcessError):
            continue
        if b"\0" in data[:8192]:
            continue  # binary content is covered by the path rules
        text = data.decode("utf-8", errors="replace")
        report.extend(f"{path}:{lineno}: {cls}" for lineno, cls in text_findings(text, patterns))
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Block commits that could leak private health or personal data.")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true", help="Check staged content (default; used by the hook)")
    mode.add_argument("--all", action="store_true", help="Check every tracked and untracked, non-ignored file")
    ap.add_argument("--terms-file", type=Path, action="append", default=[],
                    help=f"Extra private terms file (also reads {', '.join(DEFAULT_TERM_FILES)} if present)")
    ap.add_argument("--root", type=Path, help="Repository root (default: the current git repository)")
    args = ap.parse_args(argv)

    try:
        root = args.root or Path(git("rev-parse", "--show-toplevel", root=Path.cwd()).decode().strip())
        files = list_files(root, args.all)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"privacy check: cannot read the git repository ({type(exc).__name__})", file=sys.stderr)
        return 1
    terms = load_terms(root, [Path(p) for p in DEFAULT_TERM_FILES] + args.terms_file)
    report = check(root, files, staged=not args.all, terms=terms)
    if report:
        print("Privacy check FAILED. Values are not shown; inspect the files locally:", file=sys.stderr)
        for line in report:
            print(f"  {line}", file=sys.stderr)
        print("Remove the data. If it is synthetic, add it to the allow-lists in tools/privacy_check.py.",
              file=sys.stderr)
        return 1
    print(f"Privacy check passed: {len(files)} file(s), {len(terms)} private term(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
