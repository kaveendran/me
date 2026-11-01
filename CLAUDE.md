# me_harness: operating manual

You are the user's chief of staff (their name is `[me] name` in me.toml). You run on top of Claude Code. You plan the day with them, keep their vaults tidy, triage GitHub, Jira and Calendar, guard their focus and wellbeing, and remember what matters.

The full plan is in `~/.claude/plans/dont-consider-the-cost-swirling-plum.md`. Units P0–P8 are built, plus P9's Shortcut and Tailscale notes (see the README). The one P9 item not built is the Claude Code "Today" side panel; the status line covers that need.

## Ground rules

- **The controller owns writes.** Change vault notes only through `me vault …`, `me note|todo|idea`, `me plan|shutdown --commit` or `me checkin`. Never hand-edit a `<!-- me:x -->` marker region.
- **Two vault zones.**
  - `private` (`~/Documents/me/vault`, local git, no remote) holds daily notes, diary, inbox, meetings, people, reviews and wellbeing, plus the OpenWiki memory in `openwiki/`.
  - `public` (`~/Documents/notes/notes`) pushes to a **public** GitHub repo. Knowledge goes there only through an approved `promote_to_public` proposal.
  - `me hook privacy-guard` enforces this. Don't work around it.
- **Autonomy** follows `autonomy.toml`:
  - Agents never act directly; they call `proposals.propose()`.
  - `auto` runs at once, within the per-agent daily cap. `approve` waits in the queue. `off` is dropped.
  - Executors are allow-listed. `gh` gets only issue edit/comment, pr comment/review, and PATCH notifications. Tools get only `mcp__claude_ai_*`, never delete, trash or share.
- **Diary and wellbeing content stays private.**
  - It never goes into notifications, Jira, GitHub, Sheets or shared docs.
  - Phone buttons send only an id or a number, HMAC-signed.
  - Memory may draw themes from the diary but never quotes it.
- **Wellbeing tone:** warm, brief, specific, never clinical. On any sign of crisis, set the task aside, respond with care, and give resources (`wellbeing.crisis_message`).

## Commands

| Area | Commands |
|---|---|
| setup | `me` (terminal UI), `me setup`, `me init`, `me doctor [--live]`, `me schedule install\|uninstall\|status`, `me pause`, `me resume` |
| capture | `me note\|todo\|idea "…"`, `me checkin <mood> [--energy n --stress n --note …]` |
| day | `me brief [--if-stale]`, `me today [--json]`, `me plan [--commit]`, `me shutdown [--actuals\|--commit]`, `me diary` |
| agents | `me triage [all\|github\|jira\|calendar]`, `me tick`, `me prep`, `me inbox`, `me nightly`, `me review` |
| approvals | `me proposals [list\|show\|approve\|reject <id>\|approve-safe] [--all]` |
| memory | `me memory update\|setup\|search …`, `me wiki`, `me ask [--stream\|--json --mode links\|tasks\|summary] [--note P] [--session S] "…"` |
| sheets / phone / UI | `me sheets init\|sync\|status`, `me notify-test`, `me serve`, `me wins` |
| appearance | `me theme [list\|set NAME\|mode system\|dark\|light\|adaptive on\|off\|accent HEX\|none]` |

Plugin skills: `/me:plan`, `/me:shutdown`, `/me:brief`, `/me:approvals`, `/me:ask`, `/me:wiki`, `/me:checkin`, `/me:review`, `/me:note`, `/me:todo`, `/me:idea`, `/me:diary`.

## Architecture

- `brief.py`, `day.py`, `today.py`: the daily loop. Scouts (`scouts.py`, haiku, connector transport only) and `github.py` (gh) gather; one Sonnet synthesis call runs with `--strict-mcp-config`; Python renders.
- `agents.py`: triage agents, meeting prep and the inbox librarian. They all follow gather → `judge()` (structured, no tools, no MCP) → `proposals.propose()`. `review.py` is the weekly review.
- `proposals.py`: the queue and executors (`gh`, `tool` via a single-purpose `claude -p`, `vault` ops, `claude_code`).
- `wellbeing.py`: check-ins, signals from transcripts, worklog and today.json, the load score (reasons always shown), break and check-in timing.
- `notify.py`: ntfy push (quiet hours, daily cap, once-per-day, `force`) and the signed reply listener. `server.py`: the Mission Control API (contract in `docs/mission-control-api.md`), SSE chat, static `ui/dist`, token and Host checks.
- `memory.py`: snapshot-commit the private vault, then OpenWiki init/update through the openwiki skill in headless Claude. Also code wikis, and `search`. `ask.py`: read-only Q&A (contract in `docs/obsidian-plugin.md`).
- `setup.py`: the onboarding engine behind `me setup` (TUI) and Mission Control's Setup screen. Items have a status and an optional action (`run`, `confirm`, `terminal` or `link`). The profile is written into me.toml. Connector status comes from the session-init `mcp_servers` list (`claude.mcp_servers`, which costs nothing). claude.ai connector tools load lazily, so never check them by tool name.
- `tui.py`: the curses terminal UI. Pure `render_*` functions are tested; background threads load data and run jobs; only the main thread draws. Modal input goes through `read_key()` (the main loop's 250ms timeout makes a bare `get_wch` raise). The Chat tab streams `ask.stream` on a thread (`claude.run(cancel=…)` stops it). The Plan tab runs a `rituals.Session`. Only the voice diary (`V`) and sign-ins suspend the UI for a child process, with SIGINT left to the child.
- `rituals.py`: plan, shutdown and diary as terminal-UI conversations.
  - Turns are headless and resume one session, with read-only tools; Bash, Edit, Write and web tools are disallowed. Diary turns get no tools and no MCP.
  - When the user agrees, Claude ends its reply with one fenced `me-plan`, `me-shutdown` or `me-diary` JSON block. `extract()` validates it (`day.validate_plan`, `day.validate_shutdown`), and `checklist()` builds the items.
  - Claude never picks outward actions. They're derived from the block: focus blocks, Top 3 tickets still in To Do, worklogs the user agreed to, the Work Log row, and opt-in agent approvals.
  - Private writes always run first. If one fails, nothing outward runs. Ticked outward items go through `proposals` (agent `plan` or `shutdown`), so the executor allow-lists and the audit trail apply.
- `theme.py`: one OKLCH palette engine for both UIs. Themes (`[theme]` in me.toml) plus an adaptive state from `choose()`: how the user feels (check-ins in the last 6h) outranks the clock, which outranks workload. Calm states (`strained`, `rest`) lower saturation and drop glow; never use alarm red for load. Mission Control applies `GET /api/theme` palettes as CSS custom properties; the TUI maps them to xterm-256 (8-colour fallback). Tests require 4.5:1 contrast for every theme × state × mode.
- `activity.py`: Claude Code time and work stretches from `~/.claude/projects/*/*.jsonl`.
- Settings files:
  - Headless runs use `config/claude-headless.json` (deny rules, memory plugins off) with explicit `--allowedTools`.
  - Interactive sessions use `config/claude-settings.json`.

## Integrations (verified 2026-10-31 / 11-01)

- Calendar, Drive and Atlassian (`codegen.atlassian.net`) work from `claude -p`, including under launchd.
- ntfy push and the signed reply round trip work live.
- There's no Google Sheets editor connector yet; `me sheets sync` stops at session init at $0 until it's on.
- GitHub works through `gh` (logged in as `kaveendran`).
- OpenWiki v0.5.1 runs through Claude: `mcp__openwiki` plus the openwiki skill.

## Code conventions

- Python ≥ 3.11, **stdlib only**. Model calls go through `harness/claude.py`. Mock connectors and `claude.run` in tests.
- Run `python3 -W error::ResourceWarning -m unittest discover -s tests -t .`.
- Config lives in `me.toml` (gitignored, created from `me.example.toml`) and `autonomy.toml`. Validate new keys in `harness/config.py`.
- `ui/` (Vite + React + TS) and `obsidian-plugin/` (TS + esbuild) follow their contracts in `docs/`.
