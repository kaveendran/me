"""Plan, shutdown and diary in the terminal UI: Claude's block → checklist → only the ticked changes."""

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from harness import claude, db, day, proposals, rituals, tui, vault
from harness.config import parse

from .helpers import REPO, example_raw

TODAY = date.today()


def reply_with(*texts):
    """A claude.run stand-in for ritual turns: streams each text as an assistant block."""
    calls = []

    def run(prompt, **kw):
        calls.append({"prompt": prompt, **kw})
        text = texts[min(len(calls), len(texts)) - 1]
        kw["on_event"]({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})
        return claude.RunResult(ok=True, text=text, session_id="s1", cost_usd=0.05)
    return run, calls


def tool_ok(prompt, **kw):
    return claude.RunResult(ok=True, structured={"ok": True, "detail": "created"})


def block(kind, data, lead="Locked in."):
    return f"{lead}\n\n```me-{kind}\n{json.dumps(data)}\n```"


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        raw = example_raw()
        raw["zones"]["private"]["vault"] = str(self.tmp / "private")
        raw["zones"]["public"]["vault"] = str(self.tmp / "public")
        raw["jira"].update(cloud_id="c1", site="x.atlassian.net")
        raw["sheets"]["worklog_id"] = "w1"
        (self.tmp / "private").mkdir()
        (self.tmp / "public").mkdir()
        (self.tmp / "templates").symlink_to(REPO / "templates")
        self.cfg = replace(parse(raw, REPO), home=self.tmp)
        self.conn = db.connect(self.cfg.db_path)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def write_today(self):
        (self.tmp / "state" / "today.json").write_text(json.dumps({
            "date": TODAY.isoformat(), "generated_at": datetime.now().isoformat(timespec="minutes"),
            "headline": "Two meetings, one review",
            "events": [{"title": "Standup", "start": "09:00", "end": "09:15", "attendees": 5},
                       {"title": "🎯 Deep work", "start": "14:00", "end": "15:00", "attendees": 1}],
            "jira": [{"key": "CGCS-1", "summary": "Auth design", "status_category": "To Do"},
                     {"key": "CGCS-2", "summary": "Gateway", "status_category": "In Progress"}]}))


PLAN = {"energy": 3, "top3": [{"title": "Auth design", "ref": "CGCS-1", "est_min": 90},
                              {"title": "Gateway", "ref": "CGCS-2"}],
        "blocks": [{"start": "09:30", "end": "11:00", "task": "Auth design"},
                   {"start": "14:00", "end": "15:00", "task": "Gateway"}],
        "also": ["Reply to Ravi"]}


class ExtractTest(unittest.TestCase):
    def test_block_is_cut_from_the_text_and_validated(self):
        shown, data, err = rituals.extract("plan", block("plan", PLAN))
        self.assertEqual(shown, "Locked in.")
        self.assertEqual(data["top3"][0]["title"], "Auth design")
        self.assertIsNone(err)

    def test_problems(self):
        self.assertEqual(rituals.extract("plan", "Just talking.")[1:], (None, None))
        _, data, err = rituals.extract("plan", "Here:\n```me-plan\n{not json}\n```")
        self.assertIsNone(data)
        self.assertTrue(err)
        _, data, err = rituals.extract("plan", block("plan", {"top3": []}))
        self.assertIn("top3", err)
        shown, data, _ = rituals.extract("plan", block("diary", {"entry": "x"}))  # another ritual's block is dropped
        self.assertEqual((shown, data), ("Locked in.", None))
        _, data, _ = rituals.extract("diary", 'Saved?\n```me-diary\n{"entry": "Good day"}')  # unclosed fence
        self.assertEqual(data, {"entry": "Good day"})

    def test_shutdown_validation(self):
        day.validate_shutdown({"done": ["a"], "carry": [], "jira_moved": 0, "worklogs": [{"key": "AB-1", "minutes": 30}]})
        for bad in ({"done": "a"}, {"jira_moved": -1}, {"jira_moved": True}, {"worklogs": [{"key": "nope", "minutes": 30}]},
                    {"worklogs": [{"key": "AB-1", "minutes": 0}]}, {"tomorrow_first": 3}):
            with self.assertRaises(ValueError, msg=bad):
                day.validate_shutdown(bad)


class PlanTest(Base):
    def test_plan_end_to_end(self):
        self.write_today()
        pid, _ = proposals.propose(self.cfg, self.conn, {"defaults": {"level": "approve"}}, agent="calendar",
                                   action="decline", target="e1", payload={"executor": "tool", "tools": []},
                                   preview="Decline the 16:00 sync")
        run, calls = reply_with("Here's your day. Energy today, 1–5? And does this Top 3 look right?",
                                block("plan", PLAN))
        s = rituals.Session(self.cfg, "plan")
        with mock.patch("harness.claude.run", side_effect=run):
            s.open(self.conn)
            self.assertEqual(s.stage, "talk")
            self.assertTrue(calls[0]["prompt"].startswith("Start the planning session"))
            self.assertIn("Two meetings, one review", calls[0]["prompt"])
            self.assertIn("Decline the 16:00 sync", calls[0]["prompt"])
            self.assertIn("me-plan", calls[0]["append_system_prompt"])
            self.assertIn("Bash", calls[0]["disallowed_tools"])
            self.assertEqual(calls[0]["model"], self.cfg.model("interactive"))
            s.reply("3, looks right")
        self.assertEqual(calls[1]["resume"], "s1")
        self.assertEqual(calls[1]["prompt"], "3, looks right")
        self.assertEqual(s.stage, "review")
        self.assertEqual(s.messages[-1]["text"], "Locked in.")
        items = {i["id"]: i for i in s.items}
        self.assertEqual(list(items), ["plan", "focus-09:30", "focus-14:00", "jira-CGCS-1", f"approve-{pid}", "sheets"])
        self.assertTrue(items["plan"]["fixed"])
        self.assertFalse(items["focus-14:00"]["checked"])  # a focus block already starts then
        self.assertFalse(items[f"approve-{pid}"]["checked"])  # agent approvals are opt-in
        self.assertEqual(items["focus-09:30"]["payload"]["input"]["startTime"], f"{TODAY}T09:30:00")
        s.toggle(0)  # the note can't be unticked
        s.toggle(5)  # leave the Work Log out
        self.assertTrue(items["plan"]["checked"])
        self.assertFalse(items["sheets"]["checked"])

        with mock.patch("harness.claude.run", side_effect=tool_ok) as exec_run:
            s.apply(self.conn)
        self.assertEqual(s.stage, "done")
        self.assertEqual([i["status"] for i in s.items], ["done", "done", "skipped", "done", "skipped", "skipped"])
        self.assertEqual(exec_run.call_count, 2)  # one focus block, one Jira move
        self.assertEqual({tuple(c.kwargs["allowed_tools"]) for c in exec_run.call_args_list},
                         {("mcp__claude_ai_Google_Calendar__create_event",),
                          ("mcp__claude_ai_Atlassian__getTransitionsForJiraIssue", "mcp__claude_ai_Atlassian__transitionJiraIssue")})
        note = vault.note_path(self.cfg, "daily", TODAY).read_text()
        self.assertIn("- [ ] Auth design", note)
        done = {(p["agent"], p["action"]): p["status"] for p in proposals.listing(self.conn, None)}
        self.assertEqual(done, {("plan", "focus_block"): "executed", ("plan", "transition"): "executed",
                                ("calendar", "decline"): "pending"})
        self.assertEqual(s.outcome(), "plan saved")

    def test_failed_save_runs_nothing_outward(self):
        self.write_today()
        s = rituals.Session(self.cfg, "plan")
        s.context = {"today": json.loads((self.tmp / "state" / "today.json").read_text())}
        s.data, s.items, s.stage = PLAN, rituals.checklist(self.cfg, "plan", PLAN, s.context, []), "review"
        with mock.patch("harness.day.commit_plan", side_effect=OSError("disk full")), \
                mock.patch("harness.claude.run") as run:
            s.apply(self.conn)
        run.assert_not_called()
        self.assertEqual(s.items[0]["status"], "failed")
        self.assertTrue(all(i["result"] == "not run: saving failed" for i in s.items[1:]))
        self.assertEqual(s.outcome(), "plan saved; 1 item needs a look")

    def test_first_reply_after_a_failed_opening_resends_the_context(self):
        s = rituals.Session(self.cfg, "plan")
        s.context, s.stage = {"today": {"headline": "Quiet day"}}, "talk"
        run, calls = reply_with("Here's your day.")
        with mock.patch("harness.claude.run", side_effect=run):
            s.reply("what's first?")
        self.assertIn("Quiet day", calls[0]["prompt"])
        self.assertTrue(calls[0]["prompt"].endswith("The user says:\nwhat's first?"))
        self.assertIsNone(calls[0]["resume"])

    def test_stop_pressed_while_gathering_cancels_the_opening(self):
        s = rituals.Session(self.cfg, "plan")
        s.cancel.set()
        with mock.patch("harness.claude.run") as run:
            s._exchange("Start")
        run.assert_not_called()
        self.assertEqual(s.messages[-1]["error"], "cancelled")
        self.assertFalse(s.cancel.is_set())


class ShutdownTest(Base):
    def test_shutdown_carries_and_logs(self):
        day.commit_plan(self.cfg, self.conn, TODAY, PLAN)
        s = rituals.Session(self.cfg, "shutdown")
        s.context, s.session_id, s.stage = {"actuals": {"top3": [], "github": {}, "claude_code": {}, "meeting_min": 30}}, "s1", "talk"
        data = {"done": ["Auth design"], "carry": ["Gateway"], "tomorrow_first": "Gateway", "wins": ["Shipped auth"],
                "jira_moved": 1, "note": "", "worklogs": [{"key": "CGCS-1", "minutes": 90}]}
        run, _ = reply_with(block("shutdown", data, "A solid day."))
        with mock.patch("harness.claude.run", side_effect=run):
            s.reply("auth done, gateway tomorrow, yes log it")
        self.assertEqual([i["label"] for i in s.items],
                         ["Save the shutdown to today's note", "Jira: log 1h 30m on CGCS-1", "Sheets: update today's Work Log row"])
        with mock.patch("harness.claude.run", side_effect=tool_ok), \
                mock.patch("harness.sheets.sync", return_value={"status": "pending", "detail": "1 day(s) kept locally"}):
            s.apply(self.conn)
        self.assertEqual([i["status"] for i in s.items], ["done", "done", "warn"])
        self.assertIn("- [x] Auth design", vault.note_path(self.cfg, "daily", TODAY).read_text())
        tomorrow = date.fromordinal(TODAY.toordinal() + 1)
        self.assertIn("- [ ] Gateway", vault.note_path(self.cfg, "daily", tomorrow).read_text())
        self.assertEqual(proposals.listing(self.conn, None)[0]["payload"]["input"]["timeSpent"], "1h 30m")


class DiaryTest(Base):
    def test_diary_saves_privately_with_transcript(self):
        s = rituals.Session(self.cfg, "diary")
        with mock.patch("harness.claude.run") as run:
            s.open(self.conn)
        run.assert_not_called()  # the opening is local: no wait, no cost
        self.assertEqual(s.messages[0]["text"], rituals.DIARY_OPENING)
        words = " ".join(["today was long but good"] * 32)  # 160 words
        run, calls = reply_with("Thank you for sharing that.", block("diary", {"entry": "**Highlights**: shipped auth"}, "That took grit."))
        with mock.patch("harness.claude.run", side_effect=run):
            s.reply(words)
            self.assertEqual(s.stage, "talk")
            s.reply("that's all, I'm done")
        self.assertFalse(calls[0]["mcp"])
        self.assertNotIn("allowed_tools", calls[0])
        self.assertEqual(calls[0]["prompt"], words)
        self.assertEqual([(i["id"], i["checked"]) for i in s.items], [("entry", True), ("transcript", True)])
        now = datetime(TODAY.year, TODAY.month, TODAY.day, 21, 40)
        rituals.apply(self.cfg, self.conn, "diary", s.data, s.items, s.context, s.messages, now=now)
        text = vault.note_path(self.cfg, "diary", TODAY).read_text()
        self.assertIn("<!-- me:entry-2140 -->", text)
        self.assertIn("### 21:40\n**Highlights**: shipped auth", text)
        self.assertIn("> [!quote]- Transcript\n> today was long", text)
        self.assertIn("> that's all, I'm done", text)
        self.assertIn("private: true", text)

    def test_short_diary_keeps_no_transcript_and_crisis_gets_care(self):
        s = rituals.Session(self.cfg, "diary")
        s.open(self.conn)
        run, _ = reply_with("I'm really glad you told me.")
        with mock.patch("harness.claude.run", side_effect=run):
            s.reply("honestly I want to end it all")
        self.assertEqual(s.messages[2]["role"], "note")
        self.assertIn("findahelpline.com", s.messages[2]["text"])
        items = rituals.checklist(self.cfg, "diary", {"entry": "x"}, {}, s.messages)
        self.assertFalse(items[1]["checked"])


class RenderTest(unittest.TestCase):
    def text(self, lines):
        return "\n".join("".join(seg for seg, _ in line) for line in lines)

    def test_intro_and_checklist(self):
        lines, rows = tui.render_ritual(None, 80)
        self.assertIn("p  plan the day", self.text(lines))
        self.assertEqual(rows, [])
        items = [rituals._item("plan", "Save the plan to today's note", "Top 3: A", fixed=True),
                 rituals._item("focus", "Calendar: 🎯 09:30–10:30 A", "a focus block"),
                 rituals._item("jira", "Jira: move CGCS-1 to In Progress", checked=False)]
        s = SimpleNamespace(kind="plan", stage="review", items=items, data=PLAN,
                            messages=[{"role": "me", "text": "Locked in.", "tools": [], "status": "done"}])
        lines, rows = tui.render_ritual(s, 80)
        out = self.text(lines)
        for expected in ("Ready when you are", "[x] Save the plan to today's note  private · always", "[x] Calendar: 🎯",
                         "[ ] Jira: move CGCS-1"):
            self.assertIn(expected, out)
        self.assertEqual(len(rows), 3)
        items[0].update(status="done", result="saved to 2026-11-01.md")
        items[1].update(status="failed", result="calendar unavailable")
        items[2].update(status="skipped", result="not ticked")
        s.stage = "done"
        lines, rows = tui.render_ritual(s, 80)
        out = self.text(lines)
        for expected in ("✓   Save the plan", "saved to 2026-11-01.md", "✗   Calendar", "calendar unavailable", "–   Jira"):
            self.assertIn(expected, out)
        self.assertEqual(rows, [])

    def test_gathering(self):
        s = SimpleNamespace(kind="shutdown", stage="gathering", items=[], data=None, messages=[])
        self.assertIn("Pulling today's commits", self.text(tui.render_ritual(s, 80, "⠋")[0]))


if __name__ == "__main__":
    unittest.main()
