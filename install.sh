#!/usr/bin/env bash
# me_harness installer for macOS and Linux (WSL included). Windows: install.ps1.
#
#   curl -fsSL https://raw.githubusercontent.com/kaveendran/me/main/install.sh | bash
#   ./install.sh [options]          (from a clone: installs that clone)
#
# Options:
#   --dir PATH       where me_harness lives (default ~/Documents/me/me_harness, or $ME_DIR)
#   --yes            answer yes to every question (installs whatever is missing)
#   --no-ui          skip building the web UI and the Obsidian plugin (no Node.js needed)
#   --no-obsidian    don't offer to install Obsidian
#   --no-setup       don't start `me setup` at the end
#   --uninstall      remove the background jobs, the /me:* plugin and the `me` command
#                    (your settings, history and vaults are kept)
#
# Safe to run again: it updates the clone and reinstalls `me`. Missing tools (uv,
# Claude Code, gh, Node.js) go in your home folder without sudo, and downloads are
# checked against their published SHA-256 sums. Only git (Linux) needs your package
# manager. Written for the bash 3.2 that macOS ships.
set -euo pipefail

REPO_URL="${ME_REPO:-https://github.com/kaveendran/me.git}"
BRANCH="${ME_BRANCH:-}"   # empty: the repository's default branch
DIR="${ME_DIR:-$HOME/Documents/me/me_harness}"
BIN="$HOME/.local/bin"
NODE_HOME="$HOME/.local/share/me-harness/node"
NODE_LINE="latest-v22.x"
YES=0 UI=ask OBSIDIAN=1 SETUP=1 UNINSTALL=0 LOCAL=0
ORIG_PATH="$PATH"
export PATH="$BIN:$PATH"

say()  { printf '%s\n' "$*"; }
step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31mx %s\033[0m\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dir) [ $# -ge 2 ] || die "--dir needs a path"; DIR="$2"; ME_DIR="$2"; shift ;;
    --dir=*) DIR="${1#--dir=}"; ME_DIR="$DIR" ;;
    --yes|-y) YES=1 ;;
    --no-ui) UI=no ;;
    --ui) UI=yes ;;
    --no-obsidian) OBSIDIAN=0 ;;
    --no-setup) SETUP=0 ;;
    --uninstall) UNINSTALL=1 ;;
    -h|--help) sed -n '2,22p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//' || true; exit 0 ;;
    *) die "unknown option $1 (see --help)" ;;
  esac
  shift
done

# Run from a clone (not piped from curl), and no --dir: install that clone as it is.
SELF="${BASH_SOURCE[0]:-}"
if [ -z "${ME_DIR:-}" ] && [ -f "$SELF" ]; then
  here="$(cd "$(dirname "$SELF")" && pwd)"
  if [ -f "$here/pyproject.toml" ] && grep -q '^name = "me-harness"' "$here/pyproject.toml"; then
    DIR="$here" LOCAL=1
  fi
fi

# Questions go to the terminal even when this script arrives on stdin (curl | bash).
TTY=""
if (: </dev/tty) 2>/dev/null; then TTY=/dev/tty; fi

ask() {  # ask "question" [y|n]: --yes says yes; without a terminal, the default
  local default="${2:-y}" reply hint="[Y/n]"
  [ "$YES" = 1 ] && return 0
  [ "$default" = n ] && hint="[y/N]"
  if [ -z "$TTY" ]; then [ "$default" = y ]; return; fi
  printf '%s %s ' "$1" "$hint" >"$TTY"
  read -r reply <"$TTY" || reply=""
  case "${reply:-$default}" in y|Y|yes|Yes|YES) return 0 ;; *) return 1 ;; esac
}

case "$(uname -s)" in
  Darwin) OS=mac ;;
  Linux) OS=linux; if grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null; then OS=wsl; fi ;;
  *) die "This installer is for macOS and Linux. On Windows, run install.ps1 in PowerShell." ;;
esac
case "$(uname -m)" in
  x86_64|amd64) ARCH=amd64 NODE_ARCH=x64 ;;
  arm64|aarch64) ARCH=arm64 NODE_ARCH=arm64 ;;
  *) die "Unsupported processor $(uname -m)." ;;
esac

sha256() { if have sha256sum; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
fetch() { curl -fsSL --retry 3 --proto '=https' -o "$2" "$1"; }
verify() {  # verify FILE SUMS_FILE: FILE's name must be listed in SUMS_FILE with a matching hash
  local want
  want="$(awk -v f="$(basename "$1")" '$2 == f || $2 == "*"f {print $1}' "$2" | head -n 1)"
  [ -n "$want" ] || die "no published checksum for $(basename "$1")"
  [ "$(sha256 "$1")" = "$want" ] || die "checksum mismatch for $(basename "$1"); not installing it"
}

pkg_install() {  # system packages, for the few things that need them
  local sudo=""
  if [ "$(id -u)" -ne 0 ]; then have sudo || return 1; sudo="sudo"; fi
  if have apt-get; then $sudo apt-get update -qq && $sudo apt-get install -y -qq "$@"
  elif have dnf; then $sudo dnf install -y -q "$@"
  elif have yum; then $sudo yum install -y -q "$@"
  elif have pacman; then $sudo pacman -S --noconfirm --needed "$@"
  elif have zypper; then $sudo zypper --non-interactive install "$@"
  elif have apk; then $sudo apk add "$@"
  else return 1; fi
}

# ---------------------------------------------------------------- uninstall

uninstall() {
  step "Removing me_harness"
  if have me; then me schedule uninstall || warn "couldn't remove every background job"; fi
  if have claude; then
    claude plugin uninstall me@me-local >/dev/null 2>&1 || true
    claude plugin marketplace remove me-local >/dev/null 2>&1 || true
  fi
  if have uv; then uv tool uninstall me-harness >/dev/null 2>&1 || true; fi
  say "Removed the background jobs, the /me:* commands in Claude Code, and the me command."
  say "Kept your data: $DIR (me.toml, state/) and your vaults. Delete them yourself if you want them gone."
}

# ---------------------------------------------------------------- prerequisites

base_tools() {
  local missing=""
  if [ "$OS" = mac ]; then
    if ! xcode-select -p >/dev/null 2>&1 && [ ! -x /opt/homebrew/bin/git ] && [ ! -x /usr/local/bin/git ]; then
      xcode-select --install >/dev/null 2>&1 || true
      die "git comes with Apple's Command Line Tools: finish the install in the window that just opened, then run this again."
    fi
    return
  fi
  for t in git curl tar gzip; do have "$t" || missing="$missing $t"; done
  [ -z "$missing" ] && return
  # shellcheck disable=SC2086 # one word per package
  if ask "Install${missing} with your package manager (uses sudo)?" y && pkg_install $missing; then return; fi
  die "Install${missing} first, then run this again."
}

install_uv() {
  have uv && return
  step "Installing uv (runs me's Python)"
  curl -LsSf --proto '=https' https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null
  have uv || die "uv didn't install; see https://docs.astral.sh/uv/getting-started/installation/"
}

install_claude() {
  if have claude; then say "Claude Code $(claude --version 2>/dev/null | head -n 1)"; return; fi
  if ask "Claude Code isn't installed, and me runs on it. Install it now?" y; then
    step "Installing Claude Code"
    curl -fsSL --proto '=https' https://claude.ai/install.sh | bash
    have claude || warn "Claude Code didn't land on PATH; see https://code.claude.com/docs/en/setup"
  else
    warn "Install Claude Code before using me: https://code.claude.com/docs/en/setup"
  fi
}

install_gh() {
  have gh && return
  ask "The GitHub CLI (gh) isn't installed. Install it in ~/.local/bin?" y || { warn "GitHub triage needs gh: https://cli.github.com"; return; }
  step "Installing the GitHub CLI"
  local tag v name ext tmp
  tag="$(curl -fsSLI -o /dev/null -w '%{url_effective}' https://github.com/cli/cli/releases/latest)"
  v="${tag##*/v}"
  if [ "$OS" = mac ]; then name="gh_${v}_macOS_${ARCH}" ext=zip; else name="gh_${v}_linux_${ARCH}" ext=tar.gz; fi
  tmp="$(mktemp -d)"
  fetch "https://github.com/cli/cli/releases/download/v$v/$name.$ext" "$tmp/$name.$ext"
  fetch "https://github.com/cli/cli/releases/download/v$v/gh_${v}_checksums.txt" "$tmp/sums"
  verify "$tmp/$name.$ext" "$tmp/sums"
  if [ "$ext" = zip ]; then unzip -q "$tmp/$name.$ext" -d "$tmp"; else tar -xzf "$tmp/$name.$ext" -C "$tmp"; fi
  mkdir -p "$BIN"
  install -m 755 "$tmp/$name/bin/gh" "$BIN/gh"
  rm -rf "$tmp"
  say "gh $v"
}

node_ok() {
  have node && [ "$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)" -ge 20 ] 2>/dev/null && have npm
}

install_node() {
  node_ok && return 0
  ask "Node.js 20 or newer isn't installed (it builds the web UI and the Obsidian plugin). Install Node 22 in your home folder?" y || return 1
  step "Installing Node.js"
  local plat file tmp
  if [ "$OS" = mac ]; then plat=darwin; else plat=linux; fi
  tmp="$(mktemp -d)"
  fetch "https://nodejs.org/dist/$NODE_LINE/SHASUMS256.txt" "$tmp/sums"
  file="$(awk -v s="-$plat-$NODE_ARCH.tar.gz" 'substr($2, length($2) - length(s) + 1) == s {print $2}' "$tmp/sums" | head -n 1)"
  [ -n "$file" ] || die "no Node.js build for $plat-$NODE_ARCH"
  fetch "https://nodejs.org/dist/$NODE_LINE/$file" "$tmp/$file"
  verify "$tmp/$file" "$tmp/sums"
  tar -xzf "$tmp/$file" -C "$tmp"
  rm -rf "$NODE_HOME" && mkdir -p "$(dirname "$NODE_HOME")" "$BIN"
  mv "$tmp/${file%.tar.gz}" "$NODE_HOME"
  rm -rf "$tmp"
  for t in node npm npx; do ln -sf "$NODE_HOME/bin/$t" "$BIN/$t"; done
  hash -r
  node_ok || { warn "another, older Node.js comes first on your PATH: $(command -v node)"; return 1; }
  say "Node.js $(node --version)"
}

check_obsidian() {
  [ "$OBSIDIAN" = 1 ] || return 0
  case "$OS" in
    mac)
      if [ -d /Applications/Obsidian.app ] || [ -d "$HOME/Applications/Obsidian.app" ]; then return; fi
      if have brew && ask "Obsidian isn't installed. Install it with Homebrew?" y; then
        brew install --cask obsidian || warn "Homebrew couldn't install Obsidian"
        return
      fi ;;
    linux)
      if have obsidian || [ -d "$HOME/snap/obsidian" ] || { have flatpak && flatpak info md.obsidian.Obsidian >/dev/null 2>&1; }; then return; fi
      if have flatpak && ask "Obsidian isn't installed. Install it from Flathub?" y; then
        flatpak install -y flathub md.obsidian.Obsidian || warn "Flatpak couldn't install Obsidian"
        return
      fi ;;
    wsl) say "Obsidian: use the Windows app (https://obsidian.md/download) with a vault under /mnt/c."; return ;;
  esac
  say "Obsidian (optional, for your notes): https://obsidian.md/download"
}

# ---------------------------------------------------------------- me_harness

get_repo() {
  if [ "$LOCAL" = 1 ]; then
    say "Installing from this copy: $DIR"
  elif [ -d "$DIR/.git" ]; then
    step "Updating $DIR"
    if [ -n "$(git -C "$DIR" status --porcelain --untracked-files=no)" ]; then
      warn "$DIR has local changes; leaving it as it is"
    else
      git -C "$DIR" pull --ff-only --quiet || warn "couldn't fast-forward $DIR; keeping your copy"
    fi
  elif [ -e "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ]; then
    die "$DIR already exists and isn't a copy of me_harness; choose another folder with --dir"
  else
    step "Downloading me_harness to $DIR"
    mkdir -p "$(dirname "$DIR")"
    git clone --quiet ${BRANCH:+--branch "$BRANCH"} "$REPO_URL" "$DIR"
  fi
}

install_me() {
  step "Installing the me command"
  uv tool install --quiet --force --editable --python '>=3.11' "$DIR"
  uv tool update-shell >/dev/null 2>&1 || true
  hash -r
  have me || die "me didn't land on PATH ($BIN)"
  say "me is at $(command -v me)"
}

build_ui() {
  [ "$UI" = no ] && return 0
  if [ "$UI" = ask ]; then
    ask "Build the Mission Control web UI and the Obsidian plugin? (needs Node.js; about a minute)" y || return 0
  fi
  install_node || { warn "Skipped the web UI and Obsidian plugin (no Node.js). Run this installer again to add them."; return 0; }
  local d
  for d in ui obsidian-plugin; do
    step "Building $d"
    if ! (cd "$DIR/$d" && export NPM_CONFIG_UPDATE_NOTIFIER=false && { if [ -f package-lock.json ]; then npm ci --no-audit --no-fund --loglevel=error; else npm install --no-audit --no-fund --loglevel=error; fi; } && npm run build --silent); then
      warn "$d didn't build; try later: cd \"$DIR/$d\" && npm install && npm run build"
    fi
  done
}

main() {
  if [ "$UNINSTALL" = 1 ]; then uninstall; return; fi
  step "me_harness installer ($OS, $ARCH)"
  base_tools
  install_uv
  install_claude
  install_gh
  check_obsidian
  get_repo
  install_me
  build_ui

  step "Installed"
  say "  me_harness:  $DIR   (settings go in me.toml here; your data stays on this machine)"
  say "  command:     $(command -v me)"
  case ":$ORIG_PATH:" in *":$BIN:"*) ;; *) say "  Open a new terminal so your PATH includes $BIN." ;; esac
  if [ "$SETUP" = 1 ] && [ -n "$TTY" ]; then
    say ""
    say "Starting guided setup: sign in to Claude and GitHub, pick your vaults, connect your phone."
    exec me setup <"$TTY"
  fi
  say "Next: run  me setup"
}

# Sourced (by the CI tests of single steps): define everything, run nothing.
if [ -z "${BASH_SOURCE[0]:-}" ] || [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main
fi
