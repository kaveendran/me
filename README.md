# me_harness

A personal chief-of-staff that runs on top of Claude Code. It connects GitHub, Calendar, Sheets, Jira, OpenWiki and your Obsidian vaults. See `CLAUDE.md` for how it behaves, and `~/.claude/plans/dont-consider-the-cost-swirling-plum.md` for the full roadmap (P0–P9).

## Install

```sh
cd ~/Documents/me/me_harness
uv tool install --editable .     # puts `me` on PATH
me init                          # me.toml, state db, private vault at ~/Documents/me/vault
me doctor                        # add --live to test the connectors headless
```

## One-time steps for you

1. **Project settings for plain `claude` sessions in this folder.** These turn Cognee off and set the permission baseline. Sessions that `me` launches already pass them with `--settings`.
   `cp config/claude-settings.json .claude/settings.json`
2. **Protect the public notes vault.** It pushes to a public GitHub repo, so add `.gitignore` rules for the private folders:
   `me vault guard-public` (preview), then `me vault guard-public --apply`
3. **Open the private vault in Obsidian.** Choose "Open folder as vault" and pick `~/Documents/me/vault`. Daily notes and templates are preconfigured.
4. **Optional: `/me:*` skills and the privacy guard in every project.**
   `claude plugin marketplace add ~/Documents/me/me_harness && claude plugin install me@me-local`
5. **Optional: Web Clipper.** In the Obsidian Web Clipper, go to Settings → Templates → Import and choose `clipper/me-inbox.json`. Point it at the private vault.

## Daily use (so far)

```sh
me note "call the bank about the card"
me todo "review PR 42 for Orion"
me idea "voice diary on the walk home"
me diary                         # talk or type; the entry lands in Diary/<date>.md
```

## Tests

```sh
python3 -m unittest discover -s tests -t .
```
