import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness import guard

from .helpers import make_cfg


def bash(command: str, cwd: Path) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}


class GuardTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.cfg = make_cfg(self.tmp)
        self.private = self.tmp / "private"
        self.public = self.tmp / "public"

    def tearDown(self):
        self._tmp.cleanup()

    def git_init(self, path: Path) -> None:
        subprocess.run(["git", "init", "-q", str(path)], check=True)

    def test_write_diary_into_public_vault_denied(self):
        event = {"tool_name": "Write", "tool_input": {"file_path": str(self.public / "Diary" / "d.md"), "content": "x"}}
        self.assertIn("Diary/", guard.check(self.cfg, event))

    def test_edit_public_knowledge_allowed(self):
        event = {"tool_name": "Edit", "tool_input": {"file_path": str(self.public / "tech" / "n.md"), "new_string": "y"}}
        self.assertIsNone(guard.check(self.cfg, event))

    def test_copy_from_private_to_public_denied(self):
        cmd = f"cp {self.private}/Diary/a.md {self.public}/tech/"
        self.assertIn("both the private", guard.check(self.cfg, bash(cmd, self.tmp)))

    def test_shell_write_into_public_private_dir_denied(self):
        cmd = f"echo hi > {self.public}/Daily/x.md"
        self.assertIn("Daily/", guard.check(self.cfg, bash(cmd, self.tmp)))

    def test_unrelated_command_allowed(self):
        self.assertIsNone(guard.check(self.cfg, bash("ls -la", self.tmp)))

    def test_private_vault_push_and_remote_denied(self):
        self.git_init(self.private)
        self.assertIn("stay local", guard.check(self.cfg, bash("git push", self.private)))
        cmd = f"git -C {self.private} remote add origin git@github.com:x/y.git"
        self.assertIn("stay local", guard.check(self.cfg, bash(cmd, self.tmp)))

    def test_public_git_add_with_unignored_private_files_denied(self):
        self.git_init(self.public)
        (self.public / "Daily").mkdir()
        (self.public / "Daily" / "2026-11-02.md").write_text("private")
        self.assertIn("un-ignored private files", guard.check(self.cfg, bash("git add -A", self.public)))
        (self.public / ".gitignore").write_text("Daily/\n")
        self.assertIsNone(guard.check(self.cfg, bash("git add -A", self.public)))

    def test_public_git_add_naming_private_dir_denied(self):
        self.git_init(self.public)
        self.assertIn("stages a private folder", guard.check(self.cfg, bash("git add Diary/x.md", self.public)))

    def test_public_commit_on_clean_tree_allowed(self):
        self.git_init(self.public)
        self.assertIsNone(guard.check(self.cfg, bash('git commit -m "update notes"', self.public)))


class HookMainTest(unittest.TestCase):
    def test_bad_input_fails_open(self):
        out = io.StringIO()
        self.assertEqual(guard.hook_main(io.StringIO("not json"), out), 0)
        self.assertEqual(out.getvalue(), "")

    def test_deny_emits_hook_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            event = {"tool_name": "Write", "tool_input": {"file_path": f"{tmp}/public/Diary/x.md", "content": "x"}}
            out = io.StringIO()
            with mock.patch("harness.guard.config_mod.load", return_value=cfg):
                guard.hook_main(io.StringIO(json.dumps(event)), out)
            decision = json.loads(out.getvalue())["hookSpecificOutput"]
            self.assertEqual(decision["permissionDecision"], "deny")
            self.assertIn("me privacy guard", decision["permissionDecisionReason"])


if __name__ == "__main__":
    unittest.main()
