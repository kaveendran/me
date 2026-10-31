"""GitHub through the gh CLI: deterministic, no model involved."""

from __future__ import annotations

import json
import subprocess
from datetime import date, datetime, timezone

STALE_DAYS = 30
_FAILED = {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}
_PASSED = {"SUCCESS", "NEUTRAL", "SKIPPED"}


class GhError(RuntimeError):
    pass


def gh_json(*args: str, timeout: float = 30):
    try:
        p = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise GhError(str(e)) from e
    if p.returncode != 0:
        raise GhError(p.stderr.strip()[:300] or f"gh {' '.join(args)} failed")
    return json.loads(p.stdout or "null")


def age_days(iso: str, now: datetime | None = None) -> int:
    then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return ((now or datetime.now(timezone.utc)) - then).days


def _item(raw: dict) -> dict:
    return {
        "repo": raw["repository"]["nameWithOwner"],
        "number": raw["number"],
        "title": raw["title"],
        "url": raw["url"],
        "updated": raw["updatedAt"],
        "author": (raw.get("author") or {}).get("login"),
        "draft": raw.get("isDraft", False),
        "stale": age_days(raw["updatedAt"]) > STALE_DAYS,
    }


def _repo_flags(repos) -> list[str]:
    return [f"--repo={r}" for r in repos]


def ci_state(rollup: list[dict]) -> str:
    if not rollup:
        return "none"
    states = [(c.get("conclusion") or c.get("state") or c.get("status") or "").upper() for c in rollup]
    if any(s in _FAILED for s in states):
        return "failing"
    if all(s in _PASSED for s in states):
        return "passing"
    return "pending"


def review_requests(repos=()) -> list[dict]:
    raw = gh_json("search", "prs", "--review-requested=@me", "--state=open", "--limit=30",
                  "--json", "number,title,repository,url,updatedAt,author,isDraft", *_repo_flags(repos))
    return [_item(r) for r in raw]


def my_prs(repos=(), ci_limit: int = 8) -> list[dict]:
    raw = gh_json("search", "prs", "--author=@me", "--state=open", "--limit=30",
                  "--json", "number,title,repository,url,updatedAt,isDraft", *_repo_flags(repos))
    prs = [_item(r) for r in raw]
    for pr in [p for p in prs if not p["stale"]][:ci_limit]:
        try:
            pr["ci"] = ci_state(gh_json("pr", "view", pr["url"], "--json", "statusCheckRollup")["statusCheckRollup"])
        except GhError:
            pr["ci"] = "unknown"
    return prs


def assigned_issues(repos=()) -> list[dict]:
    raw = gh_json("search", "issues", "--assignee=@me", "--state=open", "--limit=30",
                  "--json", "number,title,repository,url,updatedAt", *_repo_flags(repos))
    return [_item(r) for r in raw]


def snapshot(repos=()) -> dict:
    """Everything the morning brief needs, with per-query errors kept."""
    out, errors = {}, []
    for key, fn in (("reviews", review_requests), ("my_prs", my_prs), ("issues", assigned_issues)):
        try:
            out[key] = fn(repos)
        except GhError as e:
            out[key] = []
            errors.append(f"github {key}: {e}")
    out["errors"] = errors
    return out


def day_activity(user: str, day: date) -> dict:
    d = day.isoformat()
    out, errors = {"commits": [], "merged": [], "reviewed": []}, []
    try:
        for c in gh_json("search", "commits", f"--author={user}", f"--committer-date={d}", "--limit=100",
                         "--json", "sha,repository,commit,url"):
            out["commits"].append({"repo": c["repository"]["nameWithOwner"], "sha": c["sha"][:7],
                                   "message": c["commit"]["message"].splitlines()[0][:100], "url": c["url"]})
        out["merged"] = [_item(r) for r in gh_json(
            "search", "prs", "--author=@me", f"--merged-at={d}", "--limit=50",
            "--json", "number,title,repository,url,updatedAt")]
        out["reviewed"] = [_item(r) for r in gh_json(
            "search", "prs", "--reviewed-by=@me", f"--updated={d}", "--limit=50",
            "--json", "number,title,repository,url,updatedAt,author") if (r.get("author") or {}).get("login") != user]
    except GhError as e:
        errors.append(f"github activity: {e}")
    out["errors"] = errors
    return out
