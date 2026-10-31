"""Run headless Claude Code (`claude -p`) and parse its stream-json output."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
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
    persist: bool = True,
    mcp: bool = True,
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
    if not persist:
        cmd.append("--no-session-persistence")
    if not mcp:
        # No MCP servers or connectors: their tool schemas are most of the prompt.
        cmd.append("--strict-mcp-config")
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


class _Abort(Exception):
    pass


def run(
    prompt: str,
    *,
    cwd: Path,
    timeout: float = 900,
    on_event: Callable[[dict], None] | None = None,
    require_tool_prefix: str | None = None,
    **options,
) -> RunResult:
    """Run claude -p. With require_tool_prefix, abort at session init (before
    any model call, so at no cost) when no loaded tool has that prefix."""
    claude_bin = shutil.which("claude")
    if not claude_bin:
        return RunResult(error="claude CLI not found on PATH")
    cmd = build_command(prompt, claude_bin=claude_bin, **options)
    timed_out = threading.Event()
    missing_tools = threading.Event()
    stderr_chunks: list[str] = []

    with subprocess.Popen(
        cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, bufsize=1, start_new_session=True,
    ) as proc:
        def stop() -> None:
            # Kill the whole group: MCP servers and other children hold our pipes open.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:  # group already gone, or holds a process we may not signal
                pass

        def kill() -> None:
            timed_out.set()
            stop()

        def handle(event: dict) -> None:
            if require_tool_prefix and event.get("type") == "system" and event.get("subtype") == "init":
                if not any(str(t).startswith(require_tool_prefix) for t in event.get("tools") or []):
                    missing_tools.set()
                    raise _Abort
            if on_event:
                on_event(event)

        # Drain stderr concurrently so a chatty stderr can't fill its pipe and stall stdout.
        drain = threading.Thread(target=lambda: stderr_chunks.append(proc.stderr.read()), daemon=True)
        drain.start()
        timer = threading.Timer(timeout, kill)
        timer.start()
        try:
            result = parse_events(proc.stdout, handle)
        except _Abort:
            result = RunResult()
        finally:
            timer.cancel()
        if missing_tools.is_set() or timed_out.is_set():
            stop()
            proc.wait()
            # A child forked while the parent was dying escapes the first kill and
            # keeps our pipes open; keep killing the group until it is empty.
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    break
                time.sleep(0.02)
        proc.wait()
        drain.join(timeout=5)
    stderr = "".join(stderr_chunks)
    if missing_tools.is_set():
        result.ok, result.error = False, f"no tools matching {require_tool_prefix} (connector not enabled)"
    elif timed_out.is_set():
        result.ok, result.error = False, f"timed out after {timeout:.0f}s"
    elif proc.returncode and not result.ok and stderr.strip():
        result.error = f"{result.error}: {stderr.strip()[-500:]}"
    return result
