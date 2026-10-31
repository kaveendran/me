"""Run headless Claude Code (`claude -p`) and parse its stream-json output."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RunResult:
    ok: bool = False
    text: str = ""
    structured: dict | list | None = None
    session_id: str | None = None
    cost_usd: float = 0.0
    turns: int = 0
    error: str | None = None
    tools_used: list[str] = field(default_factory=list)


def build_command(
    prompt: str,
    *,
    model: str | None = None,
    allowed_tools: Sequence[str] = (),
    disallowed_tools: Sequence[str] = (),
    max_turns: int | None = None,
    json_schema: dict | None = None,
    resume: str | None = None,
    append_system_prompt: str | None = None,
    plugin_dir: Path | None = None,
    settings: Path | None = None,
    claude_bin: str = "claude",
) -> list[str]:
    cmd = [claude_bin, "-p", prompt, "--output-format", "stream-json", "--verbose"]
    if model:
        cmd += ["--model", model]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    if disallowed_tools:
        cmd += ["--disallowedTools", ",".join(disallowed_tools)]
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]
    if json_schema is not None:
        cmd += ["--json-schema", json.dumps(json_schema)]
    if resume:
        cmd += ["--resume", resume]
    if append_system_prompt:
        cmd += ["--append-system-prompt", append_system_prompt]
    if plugin_dir:
        cmd += ["--plugin-dir", str(plugin_dir)]
    if settings:
        cmd += ["--settings", str(settings)]
    return cmd


def parse_events(lines: Iterable[str], on_event: Callable[[dict], None] | None = None) -> RunResult:
    result = RunResult()
    texts: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if on_event:
            on_event(event)
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            result.session_id = event.get("session_id")
        elif kind == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") == "text":
                    texts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    result.tools_used.append(block.get("name", ""))
        elif kind == "result":
            result.session_id = event.get("session_id") or result.session_id
            result.cost_usd = float(event.get("total_cost_usd") or 0.0)
            result.turns = int(event.get("num_turns") or 0)
            result.structured = event.get("structured_output")
            result.text = event.get("result") or "\n".join(texts)
            result.ok = event.get("subtype") == "success" and not event.get("is_error")
            if not result.ok:
                result.error = event.get("subtype") or "error"
    if not result.text:
        result.text = "\n".join(texts)
    if result.error is None and not result.ok:
        result.error = "no result event (claude exited early?)"
    return result


def run(
    prompt: str,
    *,
    cwd: Path,
    timeout: float = 900,
    on_event: Callable[[dict], None] | None = None,
    **options,
) -> RunResult:
    claude_bin = shutil.which("claude")
    if not claude_bin:
        return RunResult(error="claude CLI not found on PATH")
    cmd = build_command(prompt, claude_bin=claude_bin, **options)
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, bufsize=1,
    )
    timed_out = threading.Event()

    def kill() -> None:
        timed_out.set()
        proc.kill()

    timer = threading.Timer(timeout, kill)
    timer.start()
    try:
        result = parse_events(proc.stdout, on_event)
        stderr = proc.stderr.read()
        proc.wait()
    finally:
        timer.cancel()
    if timed_out.is_set():
        result.ok, result.error = False, f"timed out after {timeout:.0f}s"
    elif proc.returncode and not result.ok and stderr.strip():
        result.error = f"{result.error}: {stderr.strip()[-500:]}"
    return result
