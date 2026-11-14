"""Checks that agent entry points stay in sync with the canonical skill in .github/skills/."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CANONICAL = ROOT / ".github" / "skills" / "hsa-plan-decision" / "SKILL.md"
CLAUDE_STUB = ROOT / ".claude" / "skills" / "hsa-plan-decision" / "SKILL.md"


def frontmatter(path):
    text = path.read_text(encoding="utf-8")
    _, block, _ = text.split("---\n", 2)
    return block


class ClaudeSkillStubTests(unittest.TestCase):
    def test_claude_stub_frontmatter_matches_canonical_skill(self):
        # Claude Code picks the skill by this name/description, so it must match what Copilot sees.
        self.assertEqual(frontmatter(CLAUDE_STUB), frontmatter(CANONICAL))


if __name__ == "__main__":
    unittest.main()
