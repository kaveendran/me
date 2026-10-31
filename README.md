# me_harness

A personal chief-of-staff that runs on top of Claude Code. It connects GitHub, Calendar, Sheets, Jira, OpenWiki and your Obsidian vaults. It plans the day with you, triages work on its own, guards your focus and wellbeing, remembers what matters, and reaches you on your phone. See `CLAUDE.md` for how it behaves.

## Setting it up (new person, new Mac)

There are no harness accounts. me_harness reuses the logins already on the Mac: Claude (your claude.ai account and its connectors), GitHub (`gh`), and an ntfy topic for your phone. Each person runs their own copy; never share one, because the diary lives in it.

**Prerequisites:** macOS, Python 3.11+, [uv](https://docs.astral.sh/uv/), [Claude Code](https://claude.com/claude-code), the GitHub CLI `gh`, Node 20+ (for the two UIs) and Obsidian.

```sh
git clone <this repo> ~/Documents/me/me_harness && cd ~/Documents/me/me_harness
uv tool install --editable .            # puts `me` on PATH
(cd ui && npm install && npm run build) # Mission Control web UI
(cd obsidian-plugin && npm install && npm run build)
me setup                                # guided setup in the terminal UI
```

`me setup` creates `me.toml`, detects your name and GitHub login, finds your Obsidian vaults, and shows a checklist. Select a row and press **Enter** to fix it:

| Group | What it checks | How it's fixed |
|---|---|---|
| Profile | your name, helpline country | `e` opens the profile editor (name, country, which vault is private or public) |
| Accounts | Claude login, GitHub login | Enter runs `claude auth login` or `gh auth login --web` right there |
| Connectors | Google Calendar, Drive, Atlassian (Jira), Google Sheets | opens claude.ai → Settings → Connectors; `D` re-checks (free, about 3s); Jira site auto-discovery |
| Vaults | private vault, public vault protection, Obsidian plugin | creates the vault, gitignores private folders in a public repo, links the plugin |
| Phone | ntfy | install ntfy, subscribe to the topic shown (the web Setup screen has a QR code), Enter sends a test |
| Automation | Sheets, background jobs, project settings | each asks for confirmation first, since each changes something outside the repo |
| Extras | web UI, memory, `/me:*` everywhere | optional |

The same checklist is in Mission Control (http://127.0.0.1:4777 → **Setup**). It opens automatically on first run.

## What runs on its own (launchd, `me schedule status`)

| Job | When | What |
|---|---|---|
| brief | 07:30 workdays | Calendar, Jira, GitHub, carry-over and KRs → Top 3 in today's note, plus a push |
| triage | every 30 min, work hours | GitHub (CI diagnosis, review drafts, noise, labels), Jira (merged PR → Done, missing acceptance criteria), Calendar (overload, conflicts, focus protection) → approvals |
| tick | every 15 min | meeting prep notes 5–25 min before, break nudges after 100 min straight, check-in prompts (11:00 / 15:00 / 20:00) |
| shutdown-nudge | 18:00 workdays | a push asking you to run `me shutdown` |
| nightly | 22:00 daily | web clips summarised and linked, wellbeing signals, OpenWiki memory update, code wikis |
| review | Fri 17:00 | weekly review and next week's experiment |
| serve | always | Mission Control at http://127.0.0.1:4777, plus phone button replies |

Autonomy is set in `autonomy.toml`: housekeeping runs automatically, and anything outward-facing waits for your approval. `me pause` stops every job; `me resume` restarts them.

## Daily use

`me` with no arguments opens the terminal UI:
- **Tabs:** Today, Approvals, Agents, Wellbeing, Setup.
- **Keys:** `n`/`t`/`i` capture, `c` check-in, `p` plan, `s` shutdown, `d` diary, `b` brief, `a`/`x` approve or reject. Everything else is a command:

```sh
me plan              # morning planning session (Top 3, focus blocks, Jira, approvals)
me note|todo|idea "…"
me checkin 4 --energy 3
me proposals         # approval queue: approve <id> | reject <id> | approve-safe
me ask "what did I promise Ravi?"
me shutdown          # plan vs actual, carry-over, Work Log, then the diary
me diary
```

Inside Claude Code: `/me:plan`, `/me:shutdown`, `/me:brief`, `/me:approvals`, `/me:ask`, `/me:wiki`, `/me:checkin`, `/me:review`, `/me:note`, `/me:todo`, `/me:idea`, `/me:diary`.

## Extras

- **Global capture hotkey (macOS Shortcuts):**
  1. Create a new Shortcut with the actions "Ask for Input (Text)" and then "Run Shell Script" (`/Users/sabesonk/.local/bin/me note "$1"`, input passed as arguments).
  2. In the Shortcut's details, give it a keyboard shortcut.
- **Mission Control on your phone (Tailscale):**
  1. Install Tailscale on the Mac and the phone.
  2. Run `tailscale serve --bg 4777`.
  3. Add your Mac's `*.ts.net` name to `me.toml` [serve] `allowed_hosts`.
  4. Open it on the phone and choose "Add to Home Screen". It installs as a PWA.

## Tests

```sh
python3 -W error::ResourceWarning -m unittest discover -s tests -t .
```
