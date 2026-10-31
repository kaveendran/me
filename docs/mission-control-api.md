# Mission Control API

`me serve` runs a stdlib HTTP server on `127.0.0.1:4777`. It serves the built UI from `ui/dist/` and the JSON API below.

## Auth

- Every `/api/*` request must send the header `X-Me-Token: <token>`.
- The server injects the token into `index.html` as `<meta name="me-token" content="…">`. The UI reads it at startup.
- `GET /api/chat` uses EventSource, which can't send headers, so it takes `?token=<token>` instead.
- Requests whose `Host` isn't in the allowed list (`127.0.0.1:4777`, `localhost:4777`, plus configured hosts) get a 403.

Errors come back as `{"error": "message"}` with a 4xx or 5xx status.

## Endpoints

### `GET /api/today`

```json
{
  "date": "2026-11-02", "generated_at": "2026-11-02T07:30", "missing": false,
  "headline": "Two meetings, one failing PR to fix first.",
  "events":   [{"id": "1", "title": "Standup", "start": "09:30", "end": "09:45", "attendees": 6, "meet_url": ""}],
  "tomorrow": [{"id": "3", "title": "Planning", "start": "10:00", "end": "11:00", "attendees": 8, "meet_url": ""}],
  "focus_windows": [{"start": "09:45", "end": "14:00", "minutes": 255}],
  "meeting_min": 75,
  "top3_proposed": [{"title": "Fix CI on a/b#4", "why": "Blocks the release", "ref": "https://…", "est_min": 45, "source": "github"}],
  "plan": {"top3": [{"title": "Fix CI", "done": false}], "also": [{"title": "Email Ravi", "done": true}],
           "blocks": [{"start": "09:45", "end": "10:30", "task": "Fix CI"}]},
  "risks": ["Back-to-back meetings 14:00–17:00"],
  "load": {"score": 35, "band": "elevated", "reasons": ["5.5h of meetings"]},
  "proposals_pending": 3,
  "paused": false,
  "errors": []
}
```

- `missing: true` means no brief yet today. Every list is then empty, and `plan` may still be present.
- `plan` is `null` when no plan has been committed.

### `GET /api/work`

```json
{
  "reviews": [{"repo": "a/b", "number": 3, "title": "…", "url": "…", "author": "z", "stale": false}],
  "my_prs":  [{"repo": "a/b", "number": 4, "title": "…", "url": "…", "draft": false, "stale": false, "ci": "failing"}],
  "issues":  [{"repo": "a/b", "number": 9, "title": "…", "url": "…", "stale": false}],
  "jira":    [{"key": "CGCS-1", "summary": "…", "status": "In Progress", "status_category": "In Progress", "priority": "High", "url": "https://…/browse/CGCS-1"}],
  "diagnoses": [{"pr_url": "…", "summary": "…", "likely_cause": "…", "suggested_fix": "…", "at": "2026-11-02T10:31"}]
}
```

`ci` is one of `passing | failing | pending | none | unknown`, or absent.

### `GET /api/proposals?status=pending|all`

```json
{"proposals": [{
  "id": "p_3f2a9c1b07", "agent": "github", "action": "review_comment", "target": "https://github.com/a/b/pull/3",
  "preview": "Comment on a/b#3: …", "reason": "Ravi asked for review 2 days ago", "undo": "Delete the comment on GitHub",
  "level": "approve", "safe": false, "status": "pending",
  "created": "…", "decided": null, "executed": null, "result": null
}]}
```

- `status` is `pending | approved | rejected | executed | failed`.
- `safe: true` marks a reversible, private action, which the UI may batch-approve.

### `POST /api/proposals/{id}/approve`

Returns `{"ok": true, "status": "executed" | "failed", "result": "…"}`. Execution can take 5–60 s, so show a spinner.

### `POST /api/proposals/{id}/reject`

Returns `{"ok": true}`.

### `POST /api/proposals/approve-safe`

Returns `{"approved": ["p_…"], "results": {"p_…": "executed"}}`.

### `GET /api/agents`

```json
{
  "agents": [{"name": "triage-github", "label": "GitHub triage", "schedule": "every 30 min, work hours",
              "last_run": "2026-11-02T10:30:00+05:30", "status": "ok", "last_error": null,
              "runs_7d": 14, "cost_7d": 0.42}],
  "paused": false, "spend_7d": 1.83, "spend_today": 0.21
}
```

- `status` is `ok | error | never`.
- Agent names: `brief`, `triage-github`, `triage-jira`, `triage-calendar`, `prep`, `inbox`, `memory`, `review`.

### `POST /api/agents/{name}/run`

Starts the agent in the background and returns `{"ok": true}`.

### `POST /api/pause` and `POST /api/resume`

Return `{"paused": true | false}`.

### `GET /api/wellbeing`

```json
{
  "today": {"score": 35, "band": "ok | elevated | high", "reasons": ["5.5h of meetings"]},
  "days": [{"date": "2026-10-20", "mood": 3.5, "energy": 3.0, "stress": 2.0, "load": 40, "meeting_min": 180, "active_min": 240}],
  "checkins_today": [{"ts": "2026-11-02T11:00:12+05:30", "mood": 4, "energy": 3, "stress": null}],
  "wins": [{"date": "2026-10-30", "text": "Unblocked the release"}],
  "experiment": "No meetings before 11:00 on Wednesday"
}
```

- `days` covers the last 14 days, oldest first.
- `mood`, `energy` and `stress` are daily averages, or `null` when there's no check-in that day.

### `POST /api/checkin`

Body: `{"mood": 1-5, "energy": 1-5?, "stress": 1-5?, "note": "…"?}`.

Returns `{"ok": true, "message": "…"?}`. When the note suggests a crisis, `message` carries support resources. Show it prominently.

### `POST /api/note`

Body: `{"kind": "note" | "todo" | "idea", "text": "…"}`. Returns `{"ok": true, "path": "…"}`.

### `GET /api/memory/search?q=…`

Returns `{"wiki": true, "results": [{"title": "Ravi", "path": "openwiki/people/ravi.md", "snippet": "…"}]}`.

`wiki: false` means memory hasn't been generated yet.

### `POST /api/memory/visualize`

Starts `openwiki visualize` and returns `{"url": "http://127.0.0.1:4321"}`.

### `GET /api/chat?q=…&session=…&token=…`

Server-sent events. Each `data:` line is JSON:

- `{"type": "text", "text": "…"}`: append to the assistant message.
- `{"type": "tool", "name": "Read"}`: show tool activity.
- `{"type": "done", "session": "uuid", "cost": 0.012}`: pass `session` back on the next question to continue the conversation.
- `{"type": "error", "error": "…"}`
