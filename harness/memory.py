"""Memory: OpenWiki, run by Claude through the openwiki skill, over the private vault.

OpenWiki's Claude integration only supports code-mode wikis over a git repo, so the
private vault is that repo: a nightly snapshot commit, then an OpenWiki update. Each
memory page's Grounded Claims cite the note lines they came from. The same machinery
keeps code wikis fresh for repos listed in me.toml [codewiki].
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from . import claude, db
from .config import Config

INSTRUCTIONS = """# OpenWiki brief: {name}'s personal memory

This repository is a private Obsidian vault, not a codebase. Treat its notes as the source of truth
and build a **memory wiki** that a chief-of-staff agent reads before answering questions like
"what did I promise <a colleague>?" or "what was decided about the auth migration?".

Organise pages around durable things, not folders:
- `people/<name>.md`: who they are to {name}, recent interactions, open commitments both ways.
- `projects/<name>.md`: goal, status, decisions, open questions, related Jira keys and repos.
- `commitments.md`: promises made and received, with dates and status.
- `decisions.md`: decisions with date, context and the note they were made in.
- `patterns.md`: recurring themes in work and energy (what drains, what energises), stated plainly.
- `preferences.md`: how {name} likes to work.

Rules:
- Create a page only when the notes give real evidence for it. No placeholder pages.
- Cite sources: every material fact is a Claim pointing at the note lines it came from.
- Diary notes (`Diary/`) are private reflections: extract durable facts and themes only. Never quote
  diary text verbatim and never use clinical or diagnostic language.
- Daily notes have harness-written sections (brief, plan, shutdown). Prefer the user's own words and
  the shutdown section over the brief, which is only a proposal.
- Ignore templates and generated wellbeing numbers.
"""

DEFAULT_IGNORE = [".obsidian/", ".trash/", "_templates/", "Inbox/clips/", "Wellbeing/"]
TOOLS = ("mcp__openwiki", "Skill", "Read", "Write", "Edit", "Glob", "Grep",
         "Bash(git rev-parse:*)", "Bash(git status:*)", "Bash(git log:*)", "Bash(git diff:*)", "Bash(ls:*)")


def vault_root(cfg: Config) -> Path:
    return cfg.zone_for("daily").vault


def ensure_setup(cfg: Config) -> None:
    root = vault_root(cfg)
    brief = root / "openwiki" / "INSTRUCTIONS.md"
    if not brief.exists():
        brief.parent.mkdir(parents=True, exist_ok=True)
        brief.write_text(INSTRUCTIONS.replace("{name}", cfg.name), encoding="utf-8")
    ignore = root / ".openwikiignore"
    if not ignore.exists():
        rules = cfg.section("memory").get("ignore", DEFAULT_IGNORE)
        ignore.write_text("# me_harness: paths OpenWiki never reads\n" + "\n".join(rules) + "\n", encoding="utf-8")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=me_harness", "-c", "user.email=me_harness@localhost",
                           *args], capture_output=True, text=True, timeout=60)


def snapshot(repo: Path, message: str) -> bool:
    """Commit everything in a local-only repo. Returns False when nothing changed."""
    if _git(repo, "remote").stdout.strip():
        raise RuntimeError(f"{repo} has a git remote; refusing to snapshot a vault that could be pushed")
    _git(repo, "add", "-A")
    if _git(repo, "diff", "--cached", "--quiet").returncode == 0:
        return False
    r = _git(repo, "commit", "-q", "-m", message)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300])
    return True


def initialized(repo: Path) -> bool:
    return (repo / "openwiki" / "quickstart.md").exists()


def update_wiki(cfg: Config, conn, repo: Path, agent: str) -> claude.RunResult:
    mode = "Update this repository's OpenWiki for changes since its last successful run." if initialized(repo) \
        else "Initialize this repository's OpenWiki from the current source."
    prompt = (f"Use the openwiki skill. The repository root is {repo}. {mode} "
              "Follow the OpenWiki lifecycle exactly and stop when openwiki_finish returns complete, or when begin returns noop.")
    started = db.now_iso()
    r = claude.run(prompt, cwd=repo, model=cfg.model("headless"), max_turns=250, allowed_tools=TOOLS,
                   settings=cfg.headless_settings, persist=False, timeout=3600)
    db.record_run(conn, agent=agent, model=cfg.model("headless"), started=started, result=r)
    return r


def update(cfg: Config, conn) -> str:
    root = vault_root(cfg)
    ensure_setup(cfg)
    changed = snapshot(root, "me: snapshot")
    if initialized(root) and not changed:
        return "memory: no vault changes"
    r = update_wiki(cfg, conn, root, "memory")
    if r.ok:
        snapshot(root, "me: memory update")
    return f"memory: {'ok' if r.ok else r.error} (${r.cost_usd:.2f})"


def update_code_wikis(cfg: Config, conn) -> list[str]:
    out = []
    for raw in cfg.section("codewiki").get("repos", []):
        repo = Path(raw).expanduser()
        head = _git(repo, "rev-parse", "HEAD").stdout.strip()
        if not head:
            out.append(f"{repo.name}: not a git repo")
            continue
        if db.get_cursor(conn, f"codewiki.{repo}") == head:
            continue
        r = update_wiki(cfg, conn, repo, f"codewiki-{repo.name}")
        if r.ok:
            db.set_cursor(conn, f"codewiki.{repo}", head)
        out.append(f"{repo.name}: {'ok' if r.ok else r.error} (${r.cost_usd:.2f})")
    return out


def search(cfg: Config, query: str, limit: int = 20) -> dict:
    wiki = vault_root(cfg) / "openwiki"
    if not initialized(vault_root(cfg)):
        return {"wiki": False, "results": []}
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 1]
    results = []
    for page in sorted(wiki.rglob("*.md")):
        if page.name == "INSTRUCTIONS.md" or any(p.startswith(".") for p in page.relative_to(wiki).parts):
            continue
        text = page.read_text(encoding="utf-8", errors="ignore")
        if text.startswith("---\n") and (end := text.find("\n---", 4)) != -1:
            text = text[end + 4:].lstrip()  # skip OKF frontmatter: snippets should show prose
        lower = text.lower()
        hits = sum(lower.count(w) for w in words)
        if not hits:
            continue
        first = min((lower.find(w) for w in words if w in lower), default=0)
        title = next((l.lstrip("# ").strip() for l in text.splitlines() if l.startswith("#")), page.stem)
        snippet = " ".join(text[max(0, first - 80):first + 160].split())
        results.append((hits, {"title": title, "path": str(page.relative_to(vault_root(cfg))), "snippet": snippet}))
    results.sort(key=lambda h: -h[0])
    return {"wiki": True, "results": [r for _, r in results[:limit]]}
