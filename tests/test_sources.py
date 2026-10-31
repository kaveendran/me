"""GitHub parsing, Claude Code activity, the claude runner, schedule plists, config edits."""

import json
import os
import plistlib
import stat
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from harness import activity, claude, github, schedule
from harness.config import ConfigError, parse, set_value

from .helpers import REPO, example_raw, make_cfg


class GithubTest(unittest.TestCase):
    def test_ci_state(self):
        self.assertEqual(github.ci_state([]), "none")
        self.assertEqual(github.ci_state([{"conclusion": "SUCCESS"}, {"state": "SUCCESS"}]), "passing")
        self.assertEqual(github.ci_state([{"conclusion": "SUCCESS"}, {"status": "IN_PROGRESS"}]), "pending")
        self.assertEqual(github.ci_state([{"conclusion": "FAILURE"}, {"status": "IN_PROGRESS"}]), "failing")

    def test_item_marks_stale(self):
        old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat().replace("+00:00", "Z")
        raw = {"repository": {"nameWithOwner": "a/b"}, "number": 1, "title": "t", "url": "u", "updatedAt": old,
               "author": {"login": "x"}}
        self.assertTrue(github._item(raw)["stale"])


class ActivityTest(unittest.TestCase):
    def test_sessions_cap_idle_and_skip_sdk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "proj-a").mkdir()
            base = datetime.combine(date.today(), datetime.min.time()).replace(hour=10).astimezone()

            def write(name, minutes, entrypoint="cli"):
                with (root / "proj-a" / f"{name}.jsonl").open("w") as f:
                    for i, m in enumerate(minutes):
                        ts = (base + timedelta(minutes=m)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                        f.write(json.dumps({"type": "user" if i % 2 == 0 else "assistant", "timestamp": ts,
                                            "cwd": "/x/orion", "entrypoint": entrypoint}) + "\n")

            write("s1", [0, 2, 4, 64])          # 2 + 2 + capped 5 = 9 active minutes
            write("s2", [0, 30], entrypoint="sdk-cli")
            found = activity.sessions(date.today(), root)
            self.assertEqual([(s["project"], s["active_min"]) for s in found], [("orion", 9)])
            self.assertEqual(activity.by_project(found + found), {"orion": 18})


class ClaudeRunTest(unittest.TestCase):
    def fake_claude(self, tmp: Path, init_tools: list[str], pause: int = 1) -> Path:
        script = tmp / "claude"
        init = json.dumps({"type": "system", "subtype": "init", "session_id": "s", "tools": init_tools})
        result = json.dumps({"type": "result", "subtype": "success", "result": "ok", "num_turns": 1,
                             "total_cost_usd": 0.01, "session_id": "s"})
        script.write_text(f"#!/bin/sh\necho '{init}'\nsleep {pause}\necho '{result}'\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return script

    def test_require_tool_prefix_aborts_before_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            # The fake sleeps 30s before "answering"; aborting must not wait for it.
            # (macOS can add ~1.5s to the first exec of a new script, hence the loose bound.)
            self.fake_claude(Path(tmp), ["Read", "mcp__claude_ai_Google_Drive__search_files"], pause=30)
            with mock.patch.dict(os.environ, {"PATH": f"{tmp}:{os.environ['PATH']}"}):
                t0 = time.monotonic()
                r = claude.run("x", cwd=Path(tmp), require_tool_prefix="mcp__claude_ai_Google_Sheets")
            self.assertFalse(r.ok)
            self.assertIn("connector not enabled", r.error)
            self.assertLess(time.monotonic() - t0, 10)

    def test_run_success_with_matching_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.fake_claude(Path(tmp), ["mcp__claude_ai_Google_Sheets__get_values"])
            with mock.patch.dict(os.environ, {"PATH": f"{tmp}:{os.environ['PATH']}"}):
                r = claude.run("x", cwd=Path(tmp), require_tool_prefix="mcp__claude_ai_Google_Sheets")
            self.assertTrue(r.ok)
            self.assertEqual(r.text, "ok")


class ScheduleTest(unittest.TestCase):
    def test_plist_uses_workdays_and_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            job = schedule.jobs(cfg)["brief"]
            p = schedule.plist(cfg, "brief", job, "/Users/x/.local/bin/me")
            self.assertEqual(p["Label"], "me.harness.brief")
            self.assertEqual(p["ProgramArguments"], ["/Users/x/.local/bin/me", "brief", "--scheduled"])
            self.assertEqual([d["Weekday"] for d in p["StartCalendarInterval"]], [1, 2, 3, 4, 5])
            self.assertEqual(p["StartCalendarInterval"][0]["Hour"], 7)
            self.assertTrue(p["StandardOutPath"].endswith("state/logs/brief.log"))
            plistlib.dumps(p)  # serialisable

    def test_interval_keepalive_daily_weekly_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            jobs = schedule.jobs(cfg)
            self.assertEqual(set(jobs), {"brief", "shutdown-nudge", "triage", "tick", "nightly", "review", "serve"})
            serve = schedule.plist(cfg, "serve", jobs["serve"], "/me")
            self.assertTrue(serve["KeepAlive"] and serve["RunAtLoad"])
            self.assertNotIn("StartCalendarInterval", serve)
            self.assertEqual(schedule.plist(cfg, "triage", jobs["triage"], "/me")["StartInterval"], 1800)
            self.assertEqual(len(schedule.plist(cfg, "nightly", jobs["nightly"], "/me")["StartCalendarInterval"]), 7)
            self.assertEqual(schedule.plist(cfg, "review", jobs["review"], "/me")["StartCalendarInterval"],
                             [{"Weekday": 5, "Hour": 17, "Minute": 0}])
            self.assertEqual(schedule.describe(jobs["tick"]), "every 15 min")


class ConfigEditTest(unittest.TestCase):
    def test_set_value_in_section_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "me.toml"
            path.write_text('[a]\nid = ""  # note\n\n[sheets]\nworklog_id = ""\nokr_id = ""\n')
            set_value(path, "sheets", "okr_id", "abc")
            self.assertEqual(path.read_text(), '[a]\nid = ""  # note\n\n[sheets]\nworklog_id = ""\nokr_id = "abc"\n')
            with self.assertRaises(ConfigError):
                set_value(path, "a", "missing", "x")

    def test_workdays_validated(self):
        raw = example_raw()
        raw["schedule"]["workdays"] = ["Monday"]
        with self.assertRaises(ConfigError):
            parse(raw, REPO)


if __name__ == "__main__":
    unittest.main()


class McpAvailabilityTest(unittest.TestCase):
    def test_pending_counts_needs_auth_does_not(self):
        servers = [{"name": "claude.ai Google Calendar", "status": "pending"},
                   {"name": "claude.ai Notion", "status": "needs-auth"},
                   {"name": "openwiki", "status": "connected"}]
        self.assertTrue(claude.mcp_available(servers, "claude.ai Google Calendar"))
        self.assertTrue(claude.mcp_available(servers, "openwiki"))
        self.assertFalse(claude.mcp_available(servers, "claude.ai Notion"))
        self.assertFalse(claude.mcp_available(servers, "claude.ai Google Sheets"))
