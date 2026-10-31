import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

from harness import db, day, vault

from .helpers import make_cfg

DAY = date(2026, 11, 2)


def ev(title, start, end, **kw):
    return {"id": title, "title": title, "start": f"2026-11-02T{start}", "end": f"2026-11-02T{end}",
            "all_day": False, "response": "accepted", **kw}


class WindowsTest(unittest.TestCase):
    def setUp(self):
        self.bounds = (datetime(2026, 11, 2, 9, 0), datetime(2026, 11, 2, 18, 30))

    def test_focus_windows_and_meetings(self):
        events = day.timed_events([
            ev("Standup", "09:30", "09:45"),
            ev("Design review", "11:00", "12:00", attendees=5),
            ev("Declined thing", "14:00", "15:00", response="declined"),
            ev("🎯 Deep work", "15:00", "16:00"),
            ev("Late sync", "17:30", "18:00"),
            {"id": "x", "title": "Holiday", "start": "2026-11-02", "end": "2026-11-03", "all_day": True, "response": "none"},
        ], DAY)
        self.assertEqual([e["title"] for e in events], ["Standup", "Design review", "🎯 Deep work", "Late sync"])
        self.assertEqual(day.meeting_minutes(events, self.bounds), 15 + 60 + 30)
        windows = day.focus_windows(events, self.bounds)
        self.assertEqual([(w["start"], w["end"]) for w in windows], [("09:45", "11:00"), ("12:00", "17:30")])

    def test_windows_start_from_now_rounded_up(self):
        windows = day.focus_windows([], self.bounds, now=datetime(2026, 11, 2, 13, 7))
        self.assertEqual(windows[0]["start"], "13:15")
        self.assertEqual(day.focus_windows([], self.bounds, now=datetime(2026, 11, 2, 19, 0)), [])

    def test_short_gaps_dropped(self):
        events = day.timed_events([ev("A", "09:00", "17:50")], DAY)
        self.assertEqual(day.focus_windows(events, self.bounds), [])


class DayNotesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.cfg = make_cfg(self.tmp)
        self.conn = db.connect(self.tmp / "me.db")

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def test_carry_candidates_from_yesterday_inbox_and_diary(self):
        vault.write_section(self.cfg, "daily", "plan", "- [x] done thing\n- [ ] unfinished thing", date(2026, 10, 30))
        vault.capture(self.cfg, "todo", "buy milk", datetime(2026, 11, 1, 9, 0))
        vault.write_section(self.cfg, "diary", "entry-2100", "**Open loops**: \n- [ ] call dad", date(2026, 11, 1))
        c = day.carry_candidates(self.cfg, DAY)
        self.assertEqual(c["carry"], ["unfinished thing"])
        self.assertEqual(c["inbox_todos"], ["buy milk"])
        self.assertEqual(c["open_loops"], ["call dad"])

    def test_carried_section_wins_over_previous_note(self):
        vault.write_section(self.cfg, "daily", "plan", "- [ ] old", date(2026, 11, 1))
        vault.write_section(self.cfg, "daily", "carried", "- [ ] chosen", DAY)
        self.assertEqual(day.carry_candidates(self.cfg, DAY)["carry"], ["chosen"])

    def test_plan_commit_progress_and_validation(self):
        plan = {"energy": 3, "top3": [{"title": "Fix auth", "ref": "PROJ-1", "est_min": 60}, {"title": "Review PR"}],
                "blocks": [{"start": "09:30", "end": "10:30", "task": "Fix auth"}], "also": ["Email Ravi"]}
        path = day.commit_plan(self.cfg, self.conn, DAY, plan, now=datetime(2026, 11, 2, 8, 40))
        text = vault.read_section(path.read_text(), "plan")
        self.assertIn("- [ ] Fix auth · ~60m · PROJ-1", text)  # no jira site in the test config
        self.assertIn("- 09:30–10:30 Fix auth", text)
        self.assertEqual(day.plan_progress(self.cfg, DAY), (0, 2))
        planned, _ = db.get_worklog(self.conn, DAY.isoformat())
        self.assertEqual(planned["focus_min"], 60)
        for bad in ({"top3": []}, {"top3": [{"title": "x"}], "blocks": [{"start": "10:00", "end": "09:00", "task": "y"}]},
                    {"top3": [{"title": "x"}], "energy": 9}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                day.validate_plan(bad)

    def test_shutdown_marks_done_carries_and_logs(self):
        day.commit_plan(self.cfg, self.conn, DAY, {"top3": [{"title": "Fix auth"}, {"title": "Write plan"}]})
        actual = {"top3": [], "github": {"commits": [1, 2], "merged": [1], "reviewed": []},
                  "claude_code": {"active_min": 125, "by_project": {"orion": 125}}, "meeting_min": 90}
        written = day.commit_shutdown(self.cfg, self.conn, DAY, {
            "done": ["Fix auth"], "carry": ["Write plan", "Email Ravi"], "tomorrow_first": "Email Ravi",
            "wins": ["Unblocked release"], "jira_moved": 1}, actual, now=datetime(2026, 11, 2, 18, 5))
        self.assertEqual(day.plan_progress(self.cfg, DAY), (1, 2))
        text = written[0].read_text()
        shutdown = vault.read_section(text, "shutdown")
        self.assertIn("**Top 3 done**: 1/2", shutdown)
        self.assertIn("Claude Code 2.1h (orion 2.1h)", shutdown)
        tomorrow = vault.read_section(written[1].read_text(), "carried")
        self.assertEqual(tomorrow, "- [ ] Email Ravi\n- [ ] Write plan")
        planned, logged = db.get_worklog(self.conn, DAY.isoformat())
        row = day.worklog_row(DAY.isoformat(), planned, logged)
        self.assertEqual(row[:5], ["2026-11-02", "Mon", "Fix auth; Write plan", 1, 50])
        self.assertEqual(row[8:12], [2, 1, 0, 1])
        self.assertEqual(len(row), len(day.WORKLOG_HEADERS))


if __name__ == "__main__":
    unittest.main()
