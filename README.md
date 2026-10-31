# me_harness

A personal chief-of-staff that runs on top of Claude Code. It connects GitHub, Calendar, Sheets, Jira, OpenWiki and your Obsidian vaults. See `CLAUDE.md` for how it behaves, and `~/.claude/plans/dont-consider-the-cost-swirling-plum.md` for the full roadmap (P0–P9).

## Install

```sh
cd ~/Documents/me/me_harness
uv tool install --editable .     # puts `me` on PATH
me init                          # me.toml, state db, private vault at ~/Documents/me/vault
me sheets init                   # creates the Work Log and Goals & OKRs sheets in your Drive
me schedule install              # brief 07:30 and shutdown nudge 18:00, on workdays
me doctor                        # add --live to test the connectors headless
```

## One-time steps for you

1. **Project settings for plain `claude` sessions in this folder.** These give you the status line and today's context, and turn Cognee off. Sessions that `me` launches already pass them with `--settings`.
   `cp config/claude-settings.json .claude/settings.json`
2. **Protect the public notes vault.** It pushes to a public GitHub repo, so add `.gitignore` rules for the private folders:
   `me vault guard-public` (preview), then `me vault guard-public --apply`
3. **Turn on the Google Sheets connector** in claude.ai → Settings → Connectors. Until then, Work Log rows are kept locally, and `me sheets sync` pushes them all once it's on.
4. **Fill in your goals** in the *me · Goals & OKRs* sheet (one row per key result). The brief and `/me:plan` use them to choose the Top 3.
5. **Open the private vault in Obsidian.** Choose "Open folder as vault" and pick `~/Documents/me/vault`.
6. **Optional: `/me:*` skills and the privacy guard in every project.**
   `claude plugin marketplace add ~/Documents/me/me_harness && claude plugin install me@me-local`
7. **Optional: Web Clipper.** In the Obsidian Web Clipper, go to Settings → Templates → Import and choose `clipper/me-inbox.json`.

## A day with it

| When | What happens |
|---|---|
| 07:30 (automatic) | The brief lands in `Daily/<date>.md`, and you get a desktop notification. |
| Morning | `me plan`: a five-minute session to agree the Top 3. It writes focus blocks, moves Jira tickets and adds a Work Log row, each after you confirm. |
| Anytime | `me note`, `me todo`, `me idea`; tick boxes in Obsidian, and the status line tracks progress. |
| 18:00 (nudge) | `me shutdown`: plan vs actual, carry-over to tomorrow, Work Log actuals, then the diary. |
| Whenever | `me diary`: talk or type; the entry lands in `Diary/<date>.md`. |

`me pause` and `me resume` stop and restart the scheduled jobs. `me schedule uninstall` removes them.

## Tests

```sh
python3 -W error::ResourceWarning -m unittest discover -s tests -t .
```
