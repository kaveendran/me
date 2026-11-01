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

## Setup (first-run onboarding)

Both the `me setup` terminal UI and the web Setup screen are driven by the same engine (`harness/setup.py`).

### `GET /api/setup[?deep=1]`

`deep=1` also checks which claude.ai connectors are loaded. That takes about 3 s and costs nothing.

```json
{
  "complete": false,
  "profile": {"name": "Ada", "github_user": "ada", "jira_site": "acme.atlassian.net",
              "private_vault": "/Users/ada/Documents/me/vault", "public_vault": "/Users/ada/notes",
              "crisis_country": "LK", "timezone": "Asia/Colombo"},
  "vaults": [{"path": "/Users/ada/notes", "name": "notes"}],
  "ntfy": {"topic": "me-abc…", "subscribe_url": "https://ntfy.sh/me-abc…"},
  "items": [{
    "id": "github", "group": "Accounts", "label": "GitHub", "status": "ok",
    "detail": "signed in as ada",
    "action": {"id": "github_login", "label": "Sign in", "kind": "terminal", "confirm": null, "url": null}
  }]
}
```

- `status` is `ok | warn | todo | fail`.
- `group` is one of `Profile | Accounts | Connectors | Vaults | Phone | Automation | Extras`, in that display order.
- `action` is `null` when there's nothing to do. Its `kind` is one of:
  - `run`: POST it. Show a spinner, because some actions take up to 30 s.
  - `confirm`: show `confirm` (a sentence describing the side effect) and POST only when the user agrees.
  - `terminal`: POST it, and the server opens macOS Terminal running the command. Then poll `GET /api/setup` every 5 s for up to 2 minutes.
  - `link`: just open `url` in a new tab.
- `complete` is true when no item has `todo` or `fail`.

### `POST /api/setup/profile`

Body: any of `{"name", "crisis_country", "private_vault", "public_vault", "github_user"}`.

Returns `{"ok": true, "profile": {…}}`. Vault paths must be absolute or start with `~`.

### `POST /api/setup/run/{action_id}`

Returns `{"ok": true, "detail": "…"}`, or `{"ok": false, "detail": "…"}` with status 200. HTTP 400 means an unknown action.

## Theme (colours, and the adaptive accent)

One palette engine (`harness/theme.py`) drives Mission Control and the terminal UI. Colours are built in OKLCH, so every theme keeps readable contrast (text, muted, dim, accent and status colours are all at least 4.5:1 on `bg`, `surface` and `surface-2`).

With **adaptive** on, the applied colours follow a state picked from your workload and how you feel. How you feel outranks the clock, and the clock outranks workload:

| State | When | Look |
|---|---|---|
| `strained` "Take it easy" | latest check-in (last 6h) has mood ≤ 2 or stress ≥ 4, or load band is `high` | soft lavender, desaturated, **calm** |
| `rest` "Off hours" | not a workday, or outside `[schedule] work_hours` | warm, dim, **calm** |
| `busy` "Busy" | load band `elevated`, or 100+ min without a break | amber |
| `flow` "In flow" | load ≤ 30 and latest mood or energy ≥ 4 | emerald |
| `steady` "Steady" | otherwise | the theme's own accent (or `prefs.accent`) |

`calm: true` means: no background glow, no decorative motion, fewer competing colours. Heavy days get quieter, never alarm red.

### `GET /api/theme[?preview=<state id>]`

`preview` resolves the palette for that state without saving anything (used by the Appearance panel's state preview).

```json
{
  "prefs":   {"name": "aurora", "mode": "system", "adaptive": true, "accent": ""},
  "state":   {"id": "rest", "label": "Off hours", "message": "Off the clock. Work can wait until tomorrow.",
              "reasons": ["Sunday, not a workday", "working past 23:00"], "calm": true, "load": 30, "band": "ok"},
  "applied": {"id": "rest", "calm": true, "preview": false},
  "palette": {
    "dark":  {"bg": "#120c09", "surface": "#1a1310", "surface-2": "#211a17", "surface-3": "#29221f",
              "border": "#2f2825", "border-strong": "#403935", "text": "#f2edeb", "text-2": "#d1cdca",
              "muted": "#aba39e", "dim": "#938a86", "accent": "#d9a480", "accent-hover": "#edb793",
              "accent-ink": "#140e0b", "accent-2": "#db9f9e", "ok": "#6ed889", "warn": "#f5ae39",
              "bad": "#ffa098", "now": "#ff9bba", "accent-soft": "rgba(217, 164, 128, 0.14)",
              "accent-2-soft": "rgba(…)", "ok-soft": "rgba(…)", "warn-soft": "rgba(…)", "bad-soft": "rgba(…)",
              "dim-soft": "rgba(…)", "glow-1": "rgba(217, 164, 128, 0.048)", "glow-2": "rgba(…)"},
    "light": {"…": "same keys"}
  },
  "themes": [{"id": "aurora", "label": "Aurora", "description": "Indigo and teal on deep navy",
              "swatches": {"dark": ["#090d17", "#10151f", "#9da9ff", "#00ccbc"], "light": ["#f3f7ff", "#ffffff", "#5359ba", "#00766d"]}}],
  "states": [{"id": "flow", "label": "In flow", "message": "…", "calm": false}]
}
```

- `state` is always the live state, even when adaptive is off (the UI can still show it).
- `applied` is what the palette was resolved for: the preview, else the live state when adaptive is on, else `steady`.
- Palette keys map one-to-one to CSS custom properties: `palette.dark.bg` → `--bg`.
- `swatches` are `[bg, surface, accent, accent-2]` for each theme's own (steady) look.

### `POST /api/theme`

Body: any of `{"name": "graphite", "mode": "dark", "adaptive": false, "accent": "#4f6bed"}` (`accent: ""` clears it). Saves to `me.toml` `[theme]` and returns the same payload as `GET /api/theme`. Invalid values → 400.
