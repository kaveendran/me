# me_harness

A personal chief-of-staff that runs on top of Claude Code. It connects GitHub, Calendar, Sheets, Jira, OpenWiki and your Obsidian vaults. It plans the day with you, triages work on its own, guards your focus and wellbeing, remembers what matters, and reaches you on your phone. See `CLAUDE.md` for how it behaves.

## Install

```sh
cd ~/Documents/me/me_harness
uv tool install --editable .            # puts `me` on PATH
me init                                 # me.toml, state db, private vault at ~/Documents/me/vault
me sheets init                          # Work Log and Goals & OKRs sheets in your Drive
(cd ui && npm install && npm run build) # Mission Control UI
me schedule install                     # all launchd jobs (see below)
me doctor                               # add --live to test the connectors headless
```

## One-time steps for you

1. **Project settings for plain `claude` here:** `cp config/claude-settings.json .claude/settings.json`. This adds the status line and today's context, and turns Cognee off.
2. **Protect the public notes vault**, which pushes to a public GitHub repo: `me vault guard-public --apply`.
3. **Phone:** install the **ntfy** app (iOS or Android) and subscribe to the topic in `me.toml` [ntfy] `topic`. Then run `me notify-test`.
4. **Google Sheets connector:** turn it on in claude.ai → Settings → Connectors. Work Log rows wait locally until you do.
5. **Goals:** fill in the *me · Goals & OKRs* sheet, one row per key result.
6. **Obsidian:**
   - Open `~/Documents/me/vault` as a vault.
   - Install the sidebar plugin: `ln -s ~/Documents/me/me_harness/obsidian-plugin ~/Documents/me/vault/.obsidian/plugins/me-harness`, then enable it under Community plugins. Do the same for the notes vault if you want it there.
7. **Optional: `/me:*` skills and the privacy guard in every project:**
   `claude plugin marketplace add ~/Documents/me/me_harness && claude plugin install me@me-local`
8. **Optional: Web Clipper.** Import `clipper/me-inbox.json` in the Obsidian Web Clipper settings.

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
