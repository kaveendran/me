# Obsidian plugin ↔ harness contract

The plugin never talks to Claude directly. It spawns the `me` CLI, using an absolute path from the plugin settings, because Obsidian launched from the Dock, Start menu or a launcher doesn't inherit the shell PATH.

## Streaming chat

```
me ask --stream [--note /abs/path/to/note.md] [--session <id>] "question"
```

- stdout carries one JSON event per line. These are Claude Code stream-json events, passed straight through.
- Render events as follows:
  - `{"type":"assistant","message":{"content":[{"type":"text","text":"…"}, {"type":"tool_use","name":"Read", …}]}}`: append the text and show tool activity.
  - `{"type":"result","subtype":"success","result":"…","session_id":"…","total_cost_usd":0.01}`: the final event. Keep `session_id` and pass it as `--session` on the next question in the same conversation.
- Ignore any other event types.
- Exit code 0 means success. A non-zero exit means stderr holds the error.

## One-shot structured modes

These print a single JSON object on stdout, then exit.

```
me ask --json --mode links   --note /abs/note.md   → {"links": [{"phrase": "exact text in note", "target": "Existing Note Title"}], "tags": ["#tag"]}
me ask --json --mode tasks   --note /abs/note.md   → {"tasks": ["Email Ravi the Q4 draft"]}
me ask --json --mode summary --note /abs/note.md   → {"summary": "3–5 sentence summary"}
```

On failure they exit non-zero with `{"error": "…"}` on stdout.

## Deterministic writes

```
me todo "<task>"                                   → appends to today's private inbox; prints "saved to <path>"
me vault section diary <name> --date YYYY-MM-DD    (body on stdin) → replaces/creates a marker section in that day's diary
me brief --if-stale && me today                    → today's brief as Markdown on stdout
```

Section names match `[a-z0-9][a-z0-9-]*`.
