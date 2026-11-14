"""Tests for the repository privacy guard, run against a throwaway git repository."""
import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import privacy_check as pc


class PrivacyCheckTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)

    def tearDown(self):
        self._tmp.cleanup()

    def stage(self, rel, content):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        subprocess.run(["git", "add", "-f", rel], cwd=self.root, check=True)

    def run_check(self, *args):
        err, out = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            code = pc.main(["--root", str(self.root), *args])
        return code, err.getvalue() + out.getvalue()

    def test_blocks_private_files_but_allows_synthetic_fixtures(self):
        self.stage("cigna_eob_synthetic_test_data/x/input/a.pdf", b"%PDF-1.3 xref 0000000073 00000 n")  # privacy-check: ignore
        self.stage("cigna_eob_synthetic_test_data/x/expected_output/a.csv", "eob_id\nEOB_00001\n")
        self.assertEqual(self.run_check()[0], 0)
        self.stage("eob.pdf", b"%PDF-1.7\0")
        self.stage("docs/plan.docx", b"PK\0")
        self.stage(".local/notes.md", "nothing sensitive")
        code, report = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("eob.pdf: blocked_file_type(.pdf)", report)
        self.assertIn("docs/plan.docx: blocked_file_type(.docx)", report)
        self.assertIn(".local/notes.md: private_folder", report)
        self.assertNotIn("cigna_eob_synthetic_test_data", report)

    def test_allows_only_demo_screenshots_under_docs_images(self):
        self.stage("docs/images/demo-annual-comparison.png", b"\x89PNG\r\n\x1a\n")
        self.assertEqual(self.run_check()[0], 0)
        for rel in ("docs/images/my-workbook.png", "docs/demo-x.png", "docs/images/demo-x.xlsx",
                    "docs/images/demo-x.csv", "docs/images/sub/demo-x.png"):
            self.stage(rel, b"x")
        code, report = self.run_check()
        self.assertEqual(code, 1)
        self.assertNotIn("demo-annual-comparison.png", report)
        for rel in ("docs/images/my-workbook.png", "docs/demo-x.png", "docs/images/demo-x.xlsx",
                    "docs/images/demo-x.csv", "docs/images/sub/demo-x.png"):
            self.assertIn(f"{rel}: blocked_file_type", report)

    def test_flags_pii_patterns_without_printing_values(self):
        self.stage("ok.md", "C:\\Users\\YourUser\\Documents\nref 998877665544\nfoo@users.noreply.github.com\n"
                            "ssn 123-45-6789 privacy-check: ignore\n")  # privacy-check: ignore
        self.assertEqual(self.run_check()[0], 0)
        self.stage("bad.md", "mail jdoe@mail.test\ncall (919) 555-0142\nssn 123-45-6789\n"  # privacy-check: ignore
                             "member QX98765432\npath D:\\users\\jdoe\\code\n")  # privacy-check: ignore
        code, report = self.run_check()
        self.assertEqual(code, 1)
        for cls in ("bad.md:1: email", "bad.md:2: phone", "bad.md:3: ssn", "bad.md:4: long_id", "bad.md:5: user_path"):
            self.assertIn(cls, report)
        for value in ("jdoe", "555-0142", "6789", "98765432"):  # privacy-check: ignore
            self.assertNotIn(value, report)

    def test_private_terms_from_local_file(self):
        (self.root / ".local").mkdir()
        (self.root / ".local" / "private_terms.txt").write_text("# comment\nZephyrcorp\n42 Elm Street\n",
                                                               encoding="utf-8")
        self.stage("notes.md", "The Zephyrcorp plan\nLives at 42 elm street.\nzephyrcorporation is unrelated\n")
        code, report = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("notes.md:1: private_term#2", report)
        self.assertIn("notes.md:2: private_term#1", report)
        self.assertNotIn("notes.md:3", report)
        self.assertNotIn("Zephyrcorp", report)

    def test_all_mode_includes_untracked_files(self):
        (self.root / "draft.md").write_text("ssn 123-45-6789\n", encoding="utf-8")  # privacy-check: ignore
        self.assertEqual(self.run_check()[0], 0)
        code, report = self.run_check("--all")
        self.assertEqual(code, 1)
        self.assertIn("draft.md:1: ssn", report)


if __name__ == "__main__":
    unittest.main()
