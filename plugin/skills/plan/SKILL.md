---
name: plan
description: Run the morning planning session. Triage Calendar, Jira, GitHub, carry-over and inbox, agree on today's Top 3 with the user, then commit the plan (daily note, focus blocks, Jira moves, Work Log). Use for /me:plan, "plan my day", "what should I work on today", or the start of a workday.
---

# Plan the day

A short conversation, about five minutes. You bring a sharp proposal and the user decides. Talk like a chief of staff: direct, no filler.

## 1. Get the picture

1. `me brief --if-stale`. This takes about a minute when it has to rebuild; tell the user "gathering your day…" first.
2. `me today --json`. This has today's events, focus windows, proposed Top 3, suggested blocks, risks, review requests, your PRs with CI state, Jira issues, carry-over, inbox todos, open loops and KRs. Items with `stale: true` are not candidates.
3. If the brief's `errors` list is non-empty, mention it in one line (for example "Jira didn't load") and carry on.

## 2. Present (one compact message)

- **Shape of the day:** meetings and hours, focus windows, anything all-day.
- **Proposed Top 3**, each with its why (from the brief, adjusted if you see something better).
- **Also on the radar:** reviews waiting, failing CI, Jira in progress or to do, carry-over, inbox todos. Keep each to one line.
- **Risks:** overload (more than 5h of meetings, or Top 3 estimates longer than the focus windows), conflicts, slipping items.
- End by asking: "Energy today, 1–5? And does this Top 3 look right?"

## 3. Converge

- If energy ≤ 2 or meetings are over 5h, suggest cutting to Top 1–2 and say what you'd defer.
- Swap, reword and re-estimate as the user says. Fit blocks only inside focus windows. Never schedule over meetings.
- Stop once the user agrees. Don't over-polish.

## 4. Commit

First write the plan locally (no confirmation needed; it's the user's private note):

```
me plan --commit <<'EOF'
{"energy": 3,
 "top3": [{"title": "Fix flaky auth test", "ref": "PROJ-142", "est_min": 60}],
 "blocks": [{"start": "09:30", "end": "10:30", "task": "Fix flaky auth test"}],
 "also": ["Reply to Ravi about the demo"]}
EOF
```

`ref` is a Jira key or URL, or omitted. `blocks` and `also` are optional.

Then list the outward actions in one message and ask once ("Do all of these?"):

- **Calendar:** one `create_event` per block. Summary `🎯 <task>`, local start and end times today (no offset), description `Focus block · me_harness`, no attendees.
- **Jira:** for each Top 3 ticket still in To Do, call `getTransitionsForJiraIssue` and then `transitionJiraIssue` to the In Progress transition. cloudId is in `me.toml` [jira].
- **Work Log:** `me sheets sync`. If it reports pending because the Sheets connector is off, say the row is saved locally and will sync later.

Do only what the user approves. Report each result in one line.

## 5. Close

One line: the Top 3, plus "tick boxes in today's note as you go; the status line tracks it". Don't recap the whole plan.
