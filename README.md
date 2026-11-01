# me_harness

A personal chief-of-staff that runs on top of Claude Code. It connects GitHub, Calendar, Sheets, Jira, OpenWiki and your Obsidian vaults. It plans the day with you, triages work on its own, guards your focus and wellbeing, remembers what matters, and reaches you on your phone. See `CLAUDE.md` for how it behaves.

## Install (macOS, Linux, Windows)

There are no harness accounts. me_harness reuses the logins already on your computer: Claude (your claude.ai account and its connectors), GitHub (`gh`), and an ntfy topic for your phone. Each person runs their own copy; never share one, because the diary lives in it.

One command installs everything, then starts the guided setup:

| System | Run this in a terminal |
|---|---|
| **macOS** | `curl -fsSL https://raw.githubusercontent.com/kaveendran/me/main/install.sh \| bash` |
| **Linux** (Ubuntu, Debian, Fedora, Arch, openSUSE…) | same as macOS |
| **Windows 10/11** (PowerShell) | `irm https://raw.githubusercontent.com/kaveendran/me/main/install.ps1 \| iex` |
| **Windows, in WSL** | the macOS/Linux command, inside your WSL distro (see the notes below) |

What the installer does:
- **Asks before installing anything.** Missing tools go in your home folder, with no sudo or admin rights:
  - [uv](https://docs.astral.sh/uv/), which also fetches Python 3.11+ if you don't have it;
  - [Claude Code](https://claude.com/claude-code), using its official installer;
  - the GitHub CLI;
  - Node.js 22, only if you build the web UI.
- **Checks downloads.** gh and Node.js are checked against their published SHA-256 sums.
- **Uses the system only for git.** On Linux, git comes from your package manager (sudo). On macOS, it comes from Apple's Command Line Tools. On Windows, it's Git for Windows from winget, which Claude Code needs for Git Bash.
- **Clones and installs.** It clones this repo to `~/Documents/me/me_harness`, installs the `me` command into `~/.local/bin`, and adds that folder to your PATH.
- **Builds the extras.** If you say yes, it builds Mission Control and the Obsidian plugin. It also offers to install Obsidian (Homebrew, Flathub or winget).
- **Can run again.** Running it again updates the clone and reinstalls `me`.

Options (`install.sh --help`):

| Option | bash | PowerShell |
|---|---|---|
| Install somewhere else | `--dir PATH` | `-Dir PATH` |
| Say yes to everything | `--yes` | `-Yes` |
| Skip the web UI and Obsidian plugin (no Node.js) | `--no-ui` | `-NoUi` |
| Don't offer Obsidian | `--no-obsidian` | `-NoObsidian` |
| Don't start `me setup` at the end | `--no-setup` | `-NoSetup` |
| Remove the jobs, the `/me:*` plugin and the `me` command, keeping your data | `--uninstall` | `-Uninstall` |

To pass options through a pipe:
- **bash:** `curl -fsSL …/install.sh | bash -s -- --no-ui`
- **PowerShell:** `& ([scriptblock]::Create((irm …/install.ps1))) -NoUi`

From a clone, `./install.sh` or `powershell -ExecutionPolicy Bypass -File install.ps1` installs that clone as it is.

### What differs by system

| | macOS | Linux | Windows |
|---|---|---|---|
| Background jobs (`me schedule`) | launchd LaunchAgents | systemd user timers | Task Scheduler (`\me-harness\`), run windowless |
| Desktop notifications | Notification Center | `notify-send` | toast notifications |
| Terminal UI (`me`) | built in | built in | through `windows-curses` (installed with `me`) |
| Obsidian vault discovery | `~/Library/Application Support/obsidian` | `~/.config/obsidian`, Flatpak and Snap | `%APPDATA%\obsidian` |

Everywhere, jobs run only while you're logged in. A job missed while the computer slept runs at the next chance.

Notes:
- **Linux.** `me schedule install` needs a systemd user session, which every desktop distro has. To keep jobs running while you're logged out, run `loginctl enable-linger $USER`.
- **WSL.**
  - Turn systemd on first: put `[boot]` and `systemd=true` in `/etc/wsl.conf`, then run `wsl --shutdown`.
  - Jobs only run while WSL is up, and the Obsidian plugin can't reach `me` inside WSL.
  - For the full experience on Windows, use the native installer instead.
- **Privacy.** Keep the private vault out of folders that sync to a cloud: iCloud Desktop & Documents, or OneDrive's Documents folder. Obsidian Sync is end-to-end encrypted, so it's fine.

### Then: guided setup

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

## What runs on its own (`me schedule status`)

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
- **Tabs:** Today, Approvals, Agents, Wellbeing, Setup, Chat, Plan.
- **Chat:** press `/` anywhere to ask a question and get the answer right there, streamed, with the notes it read. It's read-only and multi-turn; `x` stops an answer or starts a new chat.
- **Plan, shutdown and diary:** `p`, `s` and `d` start a conversation in the Plan tab. Claude looks things up but changes nothing. When you agree, you get a checklist:
  - Your note (plan, shutdown or diary entry) is always saved, and only to the private vault.
  - Focus blocks, Jira moves and worklogs, the Work Log row and agent approvals happen only if you leave them ticked.
  - `space` ticks or unticks, `⏎` does the ticked ones, `/` goes back to talking, `x` stops an answer or ends the session.
  - `V` opens the diary in Claude Code instead, where voice works.
- **Keys:** `n`/`t`/`i` capture, `c` check-in, `b` brief, `a`/`x` approve or reject, `T` appearance, `?` every key. Everything else is a command:

```sh
me plan              # the planning session in Claude Code (the terminal UI has it built in: p)
me note|todo|idea "…"
me checkin 4 --energy 3
me proposals         # approval queue: approve <id> | reject <id> | approve-safe
me ask "what did I promise Ravi?"
me shutdown          # plan vs actual, carry-over, Work Log, then the diary (terminal UI: s)
me diary             # in Claude Code, where voice works (terminal UI: d, or V for voice)
```

Inside Claude Code: `/me:plan`, `/me:shutdown`, `/me:brief`, `/me:approvals`, `/me:ask`, `/me:wiki`, `/me:checkin`, `/me:review`, `/me:note`, `/me:todo`, `/me:idea`, `/me:diary`.

## Themes and adaptive colours

Both UIs share one palette engine. Pick a theme in the terminal UI (`T`), in Mission Control (**Appearance**), or with `me theme`:

```sh
me theme                 # current theme, and the state your day is in right now
me theme list            # aurora · graphite · fjord · ember · orchid
me theme set graphite
me theme mode dark       # system | dark | light
me theme adaptive off    # keep the theme's own accent all day
me theme accent '#e0457b'  # your own accent for steady days ('none' to clear)
```

With **adaptive** on (the default), the colours follow your workload and how you feel:

| State | When | Look |
|---|---|---|
| Take it easy | a check-in in the last 6h with mood ≤ 2 or stress ≥ 4, or a high load score | soft lavender, low saturation, no glow |
| Off hours | weekends and outside `work_hours` | warm and dim |
| Busy | elevated load, or 100+ minutes without a break | amber |
| In flow | light load and a good mood or energy check-in | emerald |
| Steady | anything else | the theme's own accent |

How you feel outranks the clock, and the clock outranks workload. Heavy days get calmer, never alarm red. The reasons are always shown (top bar in the terminal, the state pill in Mission Control). Every palette is checked for readable contrast (4.5:1) in tests.

## Extras

- **Global capture hotkey:**
  - **macOS (Shortcuts).** Create a new Shortcut with the actions "Ask for Input (Text)" and then "Run Shell Script" (`~/.local/bin/me note "$1"`, input passed as arguments). In the Shortcut's details, give it a keyboard shortcut.
  - **Linux (GNOME, KDE).** Add a custom keyboard shortcut that runs `sh -c 'me note "$(zenity --entry --title=me)"'`.
  - **Windows.** Make a desktop shortcut to `powershell -WindowStyle Hidden -Command "me note (Read-Host 'note')"`, and set its "Shortcut key" in Properties.
- **Mission Control on your phone (Tailscale):**
  1. Install Tailscale on the computer and the phone.
  2. Run `tailscale serve --bg 4777`.
  3. Add the computer's `*.ts.net` name to `me.toml` [serve] `allowed_hosts`.
  4. Open it on the phone and choose "Add to Home Screen". It installs as a PWA.

## Tests

```sh
python3 -W error::ResourceWarning -m unittest discover -s tests -t .
```

On Windows, add `-X utf8` (`python -X utf8 -W error::ResourceWarning -m unittest …`) after `pip install -e .`, which brings in `windows-curses`. CI (`.github/workflows/ci.yml`) runs the tests on macOS, Ubuntu and Windows with Python 3.11 and 3.13. It also installs the harness from scratch on each one, the way a new user does.
