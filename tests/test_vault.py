import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from harness import vault

from .helpers import make_cfg


class SectionTest(unittest.TestCase):
    TEXT = "# Day\n\n<!-- me:plan -->\n<!-- /me:plan -->\n\nmy own line\n"

    def test_fill_is_idempotent_and_keeps_outside_text(self):
        once = vault.replace_section(self.TEXT, "plan", "- [ ] ship it")
        twice = vault.replace_section(once, "plan", "- [ ] ship it")
        self.assertEqual(once, twice)
        self.assertIn("my own line", twice)
        self.assertEqual(vault.read_section(twice, "plan"), "- [ ] ship it")

    def test_replace_changes_only_the_section(self):
        first = vault.replace_section(self.TEXT, "plan", "old")
        second = vault.replace_section(first, "plan", "new")
        self.assertEqual(second, first.replace("old", "new"))

    def test_missing_section_is_appended(self):
        out = vault.replace_section("# Day\n", "brief", "hello")
        self.assertTrue(out.endswith("<!-- me:brief -->\nhello\n<!-- /me:brief -->\n"))
        self.assertEqual(vault.read_section(out, "brief"), "hello")

    def test_invalid_name(self):
        with self.assertRaises(ValueError):
            vault.replace_section("", "Bad Name", "x")


class NotesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.cfg = make_cfg(self.tmp)

    def tearDown(self):
        self._tmp.cleanup()

    def test_render_path_week(self):
        self.assertEqual(vault.render_path("Reviews/{week}.md", date(2026, 1, 1)), "Reviews/2026-W01.md")

    def test_ensure_note_uses_template(self):
        path = vault.ensure_note(self.cfg, "daily", date(2026, 11, 2))
        self.assertEqual(path, self.tmp / "private" / "Daily" / "2026-11-02.md")
        text = path.read_text()
        self.assertEqual(vault.frontmatter(text)["private"], "true")
        self.assertIn("# 2026-11-02", text)
        self.assertIn("<!-- me:plan -->", text)

    def test_write_section_preserves_user_edits(self):
        day = date(2026, 11, 2)
        path = vault.write_section(self.cfg, "daily", "plan", "v1", day)
        path.write_text(path.read_text().replace("## Notes\n", "## Notes\nhand-written\n"))
        vault.write_section(self.cfg, "daily", "plan", "v2", day)
        text = path.read_text()
        self.assertIn("hand-written", text)
        self.assertEqual(vault.read_section(text, "plan"), "v2")

    def test_capture_formats(self):
        now = datetime(2026, 11, 2, 9, 5)
        vault.capture(self.cfg, "note", "  call   mom ", now)
        vault.capture(self.cfg, "todo", "send invoice", now)
        path = vault.capture(self.cfg, "idea", "voice diary", now)
        lines = path.read_text().splitlines()[-3:]
        self.assertEqual(lines, ["- 09:05 call mom", "- [ ] send invoice (09:05)", "- 09:05 #idea voice diary"])
        with self.assertRaises(ValueError):
            vault.capture(self.cfg, "note", "   ", now)


class PrivacyTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.cfg = make_cfg(self.tmp)

    def tearDown(self):
        self._tmp.cleanup()

    def test_private_dir_in_public_vault(self):
        reason = vault.private_target_reason(self.cfg, self.tmp / "public" / "Diary" / "x.md")
        self.assertIn("Diary/ is private", reason)

    def test_public_knowledge_allowed(self):
        self.assertIsNone(vault.private_target_reason(self.cfg, self.tmp / "public" / "tech" / "x.md", "# hi"))

    def test_private_frontmatter_blocked(self):
        content = "---\ntype: diary\nprivate: true\n---\nfeelings"
        reason = vault.private_target_reason(self.cfg, self.tmp / "public" / "tech" / "x.md", content)
        self.assertIn("private: true", reason)

    def test_private_vault_and_outside_paths_allowed(self):
        self.assertIsNone(vault.private_target_reason(self.cfg, self.tmp / "private" / "Diary" / "x.md"))
        self.assertIsNone(vault.private_target_reason(self.cfg, self.tmp / "elsewhere" / "Diary" / "x.md"))


if __name__ == "__main__":
    unittest.main()
