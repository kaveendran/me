---
name: shutdown
description: Run the end-of-day shutdown. Compare the plan with what actually happened (commits, PRs, reviews, Jira moves, meetings, Claude Code time), close open loops, carry unfinished work to tomorrow, update the Work Log, then offer the diary. Use for /me:shutdown, "wrap up my day", "end of day", or "shut down".
---

# Shut down the day

The goal is to let the user stop thinking about work tonight. Be quick, honest and kind. Don't grade them.

## 1. Gather

1. `me shutdown --actuals` returns JSON with:
   - the plan's Top 3 and Also items, and their checkbox state;
   - GitHub commits, merged PRs and reviews today;
   - Claude Code active minutes per project;
   - meeting minutes.
2. Jira moves today: call `searchJiraIssuesUsingJql` with the cloudId from `me.toml` [jira], JQL `assignee = currentUser() AND status CHANGED DURING (startOfDay(), now())`, and fields `summary,status`. Skip this if it fails.

## 2. Present (one compact message)

- **Top 3:** what's ticked. Where evidence suggests an unticked item is actually done (a matching merged PR or a ticket moved to Done), ask: "Looks like X is done (PR #42 merged). Mark it?"
- **Time:** meetings, Claude Code by project, commits / PRs / reviews / Jira moves. One line each.
- Ask together:
  - "What's done? What carries to tomorrow? What's the first thing tomorrow?"
  - "Any win worth keeping?"

## 3. Commit

```
me shutdown --commit <<'EOF'
{"done": ["Fix flaky auth test"],
 "carry": ["Write migration plan"],
 "tomorrow_first": "Write migration plan",
 "wins": ["Unblocked Ravi's release"],
 "jira_moved": 2,
 "note": ""}
EOF
```

`done` titles must match the plan's Top 3 or Also titles exactly. Committing marks those items done in the note, writes the Shutdown section, puts the carry list into tomorrow's "Carried over" section with `tomorrow_first` at the top, and saves the Work Log row.

Then:
- Run `me sheets sync`. If the result is pending, the row is saved locally; mention that once.
- If the plan had Jira tickets with focus time, offer worklogs in one line ("Log 1h on PROJ-142?"). Call `addWorklogToJiraIssue` only after a yes.

## 4. Close

- Write one sincere, specific line about the day, plus the carried items so they can let go.
- Offer the diary: "Want to do your diary now?" If yes, follow the `diary` skill (`/me:diary`) in this same session.
- If it's after 20:00, or Claude Code time was over 8h, gently suggest stopping for the night. Say it once.
