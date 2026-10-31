"""P3-P6: notifications, the approval queue, agents, wellbeing, memory and ask, with externals mocked."""

import json
import subprocess
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from harness import agents, ask, claude, db, memory, notify, proposals, today, vault, wellbeing
from harness.config import parse

from .helpers import REPO, example_raw

POLICY = {"defaults": {"level": "approve", "daily_cap": 2},
          "github": {"mark_notifications_read": "auto", "merge": "off"}, "vault": {"prep_note": "auto"}}


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        raw = example_raw()
        raw["zones"]["private"]["vault"] = str(self.tmp / "private")
        raw["zones"]["public"]["vault"] = str(self.tmp / "public")
        raw["ntfy"].update(topic="t-main", reply_topic="t-reply")
        (self.tmp / "private").mkdir()
        (self.tmp / "public").mkdir()
        (self.tmp / "templates").symlink_to(REPO / "templates")
        self.cfg = replace(parse(raw, REPO), home=self.tmp)
        self.conn = db.connect(self.cfg.db_path)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()


class NotifyTest(Base):
    def test_sign_and_parse_reply(self):
        btn = notify.button(self.cfg, "Approve", "approve", "p_1")
        self.assertEqual(btn["url"], "https://ntfy.sh/t-reply")
        self.assertEqual(notify.parse_reply(self.cfg, btn["body"]), ("approve", "p_1"))
        forged = json.loads(btn["body"]) | {"value": "p_2"}
        self.assertIsNone(notify.parse_reply(self.cfg, json.dumps(forged)))
        self.assertIsNone(notify.parse_reply(self.cfg, "not json"))

    def test_quiet_hours_cross_midnight(self):
        self.assertTrue(notify.in_quiet_hours(self.cfg, datetime(2026, 11, 2, 23, 0)))
        self.assertTrue(notify.in_quiet_hours(self.cfg, datetime(2026, 11, 2, 6, 59)))
        self.assertFalse(notify.in_quiet_hours(self.cfg, datetime(2026, 11, 2, 12, 0)))

    def test_send_posts_json_and_enforces_policy(self):
        noon = datetime(2026, 11, 2, 12, 0)
        resp = mock.MagicMock(status=200)
        resp.__enter__.return_value = resp
        with mock.patch("urllib.request.urlopen", return_value=resp) as urlopen:
            out = notify.send(self.cfg, self.conn, "brief", "Hi", "msg", actions=[notify.view("Open", "https://x")], now=noon)
            self.assertEqual(out, "sent")
            body = json.loads(urlopen.call_args[0][0].data)
            self.assertEqual((body["topic"], body["title"], body["actions"][0]["action"]), ("t-main", "Hi", "view"))
            self.assertEqual(notify.send(self.cfg, self.conn, "brief", "Hi", "m", once_per_day=True, now=noon), "duplicate")
            self.assertEqual(notify.send(self.cfg, self.conn, "x", "late", "m", now=datetime(2026, 11, 2, 23, 0)), "quiet")
            self.assertEqual(notify.send(self.cfg, self.conn, "x", "urgent", "m", priority=5,
                                         now=datetime(2026, 11, 2, 23, 0)), "sent")


class ProposalsTest(Base):
    def test_levels_dedupe_and_cap(self):
        payload = {"executor": "vault", "op": "noop"}
        proposals.VAULT_OPS["noop"] = lambda cfg, p: "done"
        pid, out = proposals.propose(self.cfg, self.conn, POLICY, agent="github", action="merge", target="x", payload=payload, preview="m")
        self.assertEqual(out, "off")
        pid, out = proposals.propose(self.cfg, self.conn, POLICY, agent="github", action="review_comment", target="u",
                                     payload=payload, preview="review")
        self.assertEqual(out, "pending")
        self.assertEqual(proposals.propose(self.cfg, self.conn, POLICY, agent="github", action="review_comment", target="u",
                                           payload=payload, preview="review")[1], "duplicate")
        outcomes = [proposals.propose(self.cfg, self.conn, POLICY, agent="github", action="mark_notifications_read",
                                      target=f"t{i}", payload=payload, preview="read")[1] for i in range(3)]
        self.assertEqual(outcomes, ["executed", "executed", "pending"])  # daily cap 2, then falls back to asking
        self.assertEqual(proposals.approve(self.cfg, self.conn, pid), ("executed", "done"))
        self.assertTrue(proposals.get(self.conn, pid)["executed"])

    def test_gh_executor_allowlist(self):
        for bad in (["repo", "delete", "x"], ["pr", "merge", "1"], ["api", "-X", "DELETE", "notifications/threads/1"],
                    ["api", "-X", "PATCH", "repos/a/b"]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                proposals._check_gh(bad)
        proposals._check_gh(["api", "-X", "PATCH", "notifications/threads/42"])
        proposals._check_gh(["pr", "review", "u", "--comment", "--body", "x"])

    def test_tool_executor_rejects_destructive_tools(self):
        with self.assertRaises(ValueError):
            proposals._run_tool(self.cfg, self.conn, "x", {"tools": ["mcp__claude_ai_Google_Calendar__delete_event"],
                                                           "instruction": "x"})

    def test_failed_execution_is_recorded(self):
        proposals.VAULT_OPS["boom"] = lambda cfg, p: (_ for _ in ()).throw(RuntimeError("nope"))
        pid, _ = proposals.propose(self.cfg, self.conn, POLICY, agent="jira", action="comment", target="K-1",
                                   payload={"executor": "vault", "op": "boom"}, preview="c")
        self.assertEqual(proposals.approve(self.cfg, self.conn, pid), ("failed", "nope"))


class AgentsTest(Base):
    def write_today(self, events, meeting_min=60, jira=()):
        today.save(self.cfg, {"date": date.today().isoformat(), "generated_at": datetime.now().isoformat(timespec="minutes"),
                              "events": events, "meeting_min": meeting_min, "jira": list(jira), "reviews": [], "my_prs": []})

    def test_prep_writes_note_once_and_links_jira(self):
        now = datetime.combine(date.today(), datetime.min.time()).replace(hour=10)
        self.write_today([{"id": "1", "title": "Auth migration sync", "start": "10:15", "end": "10:45", "attendees": 4,
                           "attendee_names": ["Ravi", "Ama"], "meet_url": "https://meet/x", "response": "accepted"}],
                         jira=[{"key": "CGCS-9", "summary": "Plan the auth migration", "status_category": "To Do"}])
        with mock.patch("harness.notify.send", return_value="sent") as send:
            self.assertEqual(agents.prep_meetings(self.cfg, self.conn, POLICY, now)["notes"], 1)
            self.assertEqual(agents.prep_meetings(self.cfg, self.conn, POLICY, now)["notes"], 0)
        path = agents.meeting_path(self.cfg, now.date(), "10:15", "Auth migration sync")
        text = path.read_text()
        self.assertIn("Ravi, Ama", text)
        self.assertIn("CGCS-9", text)
        self.assertTrue(send.call_args.kwargs["click"].startswith("obsidian://open?vault=private&file=Meetings"))

    def test_calendar_overload_proposes_declines_and_focus(self):
        now = datetime.combine(date.today(), datetime.min.time()).replace(hour=8)
        events = [{"id": f"e{i}", "title": f"Meeting {i}", "start": f"{9 + i}:00".zfill(5), "end": f"{9 + i}:55".zfill(5),
                   "attendees": 8, "response": "accepted" if i else "tentative", "organizer_self": False} for i in range(6)]
        self.write_today(events, meeting_min=330)
        with mock.patch("harness.notify.send", return_value="sent"):
            out = agents.triage_calendar(self.cfg, self.conn, POLICY, now)
        pending = {p["action"] for p in proposals.listing(self.conn, "pending")}
        self.assertEqual(pending, {"decline", "focus_block"})
        self.assertEqual(out["proposals"], 2)

    def test_jira_merged_pr_proposes_done(self):
        detail = {"issues": [{"key": "CGCS-7", "summary": "Fix login", "status": "In Progress", "status_category": "In Progress",
                              "has_acceptance_criteria": True}], "errors": []}
        merged = [{"repo": "a/b", "number": 5, "title": "CGCS-7: fix login redirect", "url": "u"}]
        with mock.patch("harness.scouts.jira_detail", return_value=detail), mock.patch("harness.github.merged_since", return_value=merged):
            agents.triage_jira(self.cfg, self.conn, POLICY)
        [p] = proposals.listing(self.conn, "pending")
        self.assertEqual((p["action"], p["target"]), ("transition", "CGCS-7"))
        self.assertIn("transitionJiraIssue", p["payload"]["tools"][1])


class WellbeingTest(Base):
    def test_load_score_reasons_and_bands(self):
        quiet = {"meeting_min": 60, "longest_streak_min": 40, "active_min": 200, "late_events": 0, "weekend_work": False,
                 "mood": 4, "energy": 4, "stress": 2, "skipped_shutdowns": 0}
        self.assertEqual(wellbeing.load_score(quiet), {"score": 0, "band": "ok", "reasons": []})
        heavy = quiet | {"meeting_min": 330, "late_events": 9, "mood": 2, "longest_streak_min": 170}
        s = wellbeing.load_score(heavy)
        self.assertEqual(s["band"], "high")
        self.assertIn("working past 23:00", s["reasons"])

    def test_checkin_validation_and_crisis_message(self):
        with self.assertRaises(ValueError):
            wellbeing.record_checkin(self.cfg, self.conn, 7)
        self.assertIsNone(wellbeing.record_checkin(self.cfg, self.conn, 3, note="long day"))
        msg = wellbeing.record_checkin(self.cfg, self.conn, 1, note="I want to hurt myself")
        self.assertIn("findahelpline.com", msg)
        self.assertEqual(len(wellbeing.checkins(self.conn, date.today())), 2)
        self.assertIn("mood 3/5", vault.note_path(self.cfg, "wellbeing").read_text())

    def test_checkin_due_once_per_slot(self):
        now = datetime.combine(date.today(), datetime.min.time()).replace(hour=11, minute=5)
        self.assertEqual(wellbeing.checkin_due(self.cfg, self.conn, now), "11:00")
        db.set_cursor(self.conn, f"checkin.{now.date()}.11:00", "sent")
        self.assertIsNone(wellbeing.checkin_due(self.cfg, self.conn, now))


class MemoryAskTest(Base):
    def test_snapshot_refuses_repo_with_remote(self):
        repo = self.tmp / "private"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "a.md").write_text("x")
        self.assertTrue(memory.snapshot(repo, "s"))
        self.assertFalse(memory.snapshot(repo, "s"))
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "https://example.com/x.git"], check=True)
        with self.assertRaises(RuntimeError):
            memory.snapshot(repo, "s")

    def test_search_wiki_pages(self):
        wiki = self.tmp / "private" / "openwiki"
        (wiki / "people").mkdir(parents=True)
        (wiki / "quickstart.md").write_text("# Quickstart\n")
        (wiki / "people" / "ravi.md").write_text("# Ravi\nPromised Ravi the Q4 draft by Monday.")
        out = memory.search(self.cfg, "ravi draft")
        self.assertTrue(out["wiki"])
        self.assertEqual(out["results"][0]["title"], "Ravi")

    def test_links_mode_filters_invalid_suggestions(self):
        (self.tmp / "public" / "OAuth.md").write_text("x")
        note = self.tmp / "private" / "n.md"
        note.write_text("We discussed OAuth flows and [[JWT]] today.")
        fake = claude.RunResult(ok=True, structured={"links": [{"phrase": "OAuth", "target": "OAuth"},
                                                               {"phrase": "flows", "target": "Nope"},
                                                               {"phrase": "absent", "target": "OAuth"}], "tags": ["auth"]})
        with mock.patch("harness.claude.run", return_value=fake):
            out = ask.structured(self.cfg, "links", note)
        self.assertEqual(out, {"links": [{"phrase": "OAuth", "target": "OAuth"}], "tags": ["#auth"]})


if __name__ == "__main__":
    unittest.main()
