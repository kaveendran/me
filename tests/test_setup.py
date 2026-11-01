"""Setup engine, Setup API and the terminal UI's renderers."""

import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from dataclasses import replace
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from harness import db, server, setup, tui
from harness.config import load

from .helpers import REPO

QUIET = {  # keep the engine off the network and away from launchctl
    "harness.setup.claude_account": {"loggedIn": True, "email": "ada@example.com", "authMethod": "claude.ai"},
    "harness.setup.github_login": "ada",
    "harness.setup.detected_name": "Ada",
    "harness.schedule.status": ["me.harness.brief: not installed"],
    "harness.setup._plugin_installed": False,
}


def quiet():
    stack = [mock.patch(target, return_value=value) for target, value in QUIET.items()]
    for p in stack:
        p.start()
    return stack


class SetupBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name).resolve()
        for name in ("templates", "config", "plugin"):
            (self.home / name).symlink_to(REPO / name)
        example = (REPO / "me.example.toml").read_text().replace("~/Documents/me/vault", str(self.home / "vault"))
        (self.home / "me.example.toml").write_text(example)
        (self.home / "autonomy.toml").write_text((REPO / "autonomy.toml").read_text())
        self.patches = quiet()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self._tmp.cleanup()


class EngineTest(SetupBase):
    def test_first_run_creates_config_with_detected_defaults(self):
        cfg = setup.ensure_config(self.home)
        self.assertEqual((cfg.name, cfg.section("github")["user"]), ("Ada", "ada"))
        self.assertTrue((self.home / "vault" / "Daily").is_dir())
        self.assertNotIn("public", cfg.zones)

    def test_save_profile_validates_and_writes(self):
        cfg = setup.ensure_config(self.home)
        for bad in ({"crisis_country": "Lanka"}, {"public_vault": "relative/path"}, {"private_vault": ""}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                setup.save_profile(cfg, bad)
        public = self.home / "notes"
        public.mkdir()
        cfg = setup.save_profile(cfg, {"name": "Ada L", "crisis_country": "lk", "public_vault": str(public)})
        self.assertEqual(setup.profile(cfg)["crisis_country"], "LK")
        self.assertTrue(cfg.zones["public"].public)
        cfg = setup.save_profile(cfg, {"public_vault": ""})
        self.assertNotIn("public", cfg.zones)

    def test_items_statuses_and_actions(self):
        cfg = setup.ensure_config(self.home)
        with closing(db.connect(cfg.db_path)) as conn:
            found = {i.id: i for i in setup.items(cfg, conn)}
            self.assertEqual(found["claude"].status, "ok")
            self.assertEqual(found["calendar"].action.id, "check_connectors")  # not probed yet
            self.assertEqual(found["schedule"].action.kind, "confirm")
            self.assertEqual(found["public_vault"].status, "warn")
            self.assertFalse(setup.complete(list(found.values())))
            db.set_cursor(conn, "setup.connectors", json.dumps({"claude.ai Google Calendar": "pending",
                                                                "claude.ai Google Drive": "connected",
                                                                "claude.ai Atlassian": "needs-auth"}))
            found = {i.id: i for i in setup.items(cfg, conn)}
            self.assertEqual(found["calendar"].status, "ok")
            self.assertEqual((found["jira"].status, found["jira"].action.kind), ("todo", "link"))
            self.assertEqual(found["sheets"].status, "warn")  # optional

    def test_actions(self):
        cfg = setup.ensure_config(self.home)
        public = self.home / "notes"
        public.mkdir()
        cfg = setup.save_profile(cfg, {"public_vault": str(public)})
        with closing(db.connect(cfg.db_path)) as conn:
            out = setup.run_action(cfg, conn, "protect_public")
            self.assertTrue(out["ok"])
            self.assertIn("Diary/", (public / ".gitignore").read_text())
            self.assertEqual(setup.run_action(cfg, conn, "project_settings")["detail"], "installed .claude/settings.json")
            self.assertIn("already exists", setup.run_action(cfg, conn, "project_settings")["detail"])
            with mock.patch("harness.setup.open_terminal") as term:
                self.assertTrue(setup.run_action(cfg, conn, "github_login")["ok"])
                self.assertEqual(term.call_args[0][0][:3], ["gh", "auth", "login"])
            with self.assertRaises(KeyError):
                setup.run_action(cfg, conn, "rm_rf")


class SetupApiTest(SetupBase):
    def test_endpoints(self):
        cfg = setup.ensure_config(self.home)
        (self.home / "ui" / "dist").mkdir(parents=True)
        secret = server.token(cfg)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
        port = httpd.server_address[1]
        httpd.RequestHandlerClass = server.make_handler(cfg, secret, {f"127.0.0.1:{port}"}, self.home / "ui" / "dist")
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        def call(path, body=None):
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode() if body is not None else None,
                                         method="POST" if body is not None else "GET", headers={"X-Me-Token": secret})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                with e:
                    return e.code, json.loads(e.read())
        try:
            status, snap = call("/api/setup")
            self.assertEqual(status, 200)
            self.assertEqual(snap["profile"]["name"], "Ada")
            self.assertIn("subscribe_url", snap["ntfy"])
            self.assertEqual(call("/api/setup/profile", {"name": "Grace"})[1]["profile"]["name"], "Grace")
            self.assertEqual(call("/api/setup/profile", {"crisis_country": "xyz"})[0], 400)
            self.assertEqual(call("/api/setup/run/rm_rf", {})[0], 400)
            self.assertTrue(call("/api/setup/run/project_settings", {})[1]["ok"])
        finally:
            httpd.shutdown()
            httpd.server_close()


class TuiRenderTest(unittest.TestCase):
    def text(self, lines):
        return "\n".join("".join(seg for seg, _ in line) for line in lines)

    def test_sparkline(self):
        self.assertEqual(tui.sparkline([1, 3, 5], 1, 5), "▁▄█")
        self.assertEqual(tui.sparkline([None, 2, None], 0, 4), "·▄·")
        self.assertEqual(tui.sparkline([None, None]), "··")

    def test_today_missing_and_planned(self):
        self.assertIn("No brief yet", self.text(tui.render_today({"missing": True}, 80)))
        t = {"missing": False, "headline": "Busy day", "events": [{"title": "Standup", "start": "09:30", "end": "09:45", "attendees": 6}],
             "meeting_min": 15, "load": {"score": 40, "band": "elevated"}, "focus_windows": [],
             "plan": {"top3": [{"title": "Fix CI", "done": True}, {"title": "Review", "done": False}]},
             "risks": ["overload"], "proposals_pending": 2}
        out = self.text(tui.render_today(t, 80, now=datetime(2026, 11, 2, 9, 0)))
        for expected in ("Busy day", "09:00 ── now ──", "09:30–09:45  Standup", "Top 3 1/2", "✓ Fix CI", "2 approvals waiting"):
            self.assertIn(expected, out)

    def test_lists_return_selectable_rows(self):
        lines, rows = tui.render_approvals([{"agent": "jira", "action": "comment", "safe": False, "preview": "Comment\nmore",
                                             "reason": "no AC"}], 80)
        self.assertEqual(rows, [2])
        self.assertIn("jira/comment", self.text(lines))
        self.assertIn("1 waiting", self.text(lines))
        lines, rows = tui.render_setup({"complete": False, "profile": {"name": "Ada"}, "ntfy": {"topic": "t"},
                                        "items": [{"id": "a", "group": "Accounts", "label": "GitHub", "status": "todo",
                                                   "detail": "not signed in", "action": {"label": "Sign in"}},
                                                  {"id": "b", "group": "Vaults", "label": "Private vault", "status": "ok",
                                                   "detail": "/v", "action": None}]}, 100)
        self.assertEqual(len(rows), 2)
        out = self.text(lines)
        self.assertIn("1/2 ready", out)
        self.assertIn("○ GitHub", out)
        self.assertIn("[Sign in]", out)
        lines, _ = tui.render_agents({"paused": True, "agents": []}, 80)
        self.assertIn("Paused", self.text(lines))

    def test_state_banner_heading_and_meter(self):
        self.assertEqual(tui.render_state({"id": "steady", "label": "Steady", "message": "x"}, 80), [])
        out = self.text(tui.render_state({"id": "busy", "label": "Busy", "message": "Pace yourself.",
                                          "reasons": ["5.0h of meetings", "120 min without a break"]}, 80))
        self.assertIn("▲ Busy  Pace yourself.", out)
        self.assertIn("5.0h of meetings · 120 min without a break", out)
        line = tui.heading("Top 3", 20, "1/3")
        self.assertEqual(len(self.text([line])), 20)
        self.assertEqual(self.text([tui.meter(50, "elevated", 10)]), "━━━━━━━━━━ 50 elevated")
        self.assertEqual(tui.meter(50, "elevated", 10)[0], ("━━━━━", "warn"))
        self.assertEqual(tui.cells("🎯 ab"), 5)


if __name__ == "__main__":
    unittest.main()
