/**
 * Pure parsing helpers for the `me` CLI's output. No Obsidian or Node imports,
 * so the unit tests can run them directly under plain Node.
 */

/** Splits a stream of text chunks into complete lines, holding back a partial last line. */
export class LineBuffer {
  private pending = "";

  /** Feed a chunk; returns every line it completed (without the newline). */
  push(chunk: string): string[] {
    if (!chunk) return [];
    const data = this.pending + chunk;
    const parts = data.split("\n");
    this.pending = parts.pop() ?? "";
    return parts.map(stripCR);
  }

  /** Returns the unterminated tail, if any, and resets the buffer. */
  flush(): string[] {
    const rest = this.pending;
    this.pending = "";
    return rest.length > 0 ? [stripCR(rest)] : [];
  }
}

function stripCR(line: string): string {
  return line.endsWith("\r") ? line.slice(0, -1) : line;
}

export interface ToolUse {
  name: string;
  /** Human label, e.g. "Read" or "search_issues (atlassian)". */
  label: string;
  /** Short hint such as a file basename or a search pattern. May be empty. */
  detail: string;
}

export interface AssistantEvent {
  type: "assistant";
  texts: string[];
  tools: ToolUse[];
  /** True when the message comes from a sub-agent (parent_tool_use_id set). */
  nested: boolean;
}

export interface ResultEvent {
  type: "result";
  ok: boolean;
  subtype: string;
  result: string;
  sessionId?: string;
  costUsd?: number;
  durationMs?: number;
}

export type StreamEvent = AssistantEvent | ResultEvent;

type Json = Record<string, unknown>;

function isObject(v: unknown): v is Json {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function num(v: unknown): number | undefined {
  return typeof v === "number" && Number.isFinite(v) ? v : undefined;
}

/**
 * Parses one stdout line of `me ask --stream` (Claude Code stream-json).
 * Returns null for blank lines, non-JSON noise and event types we ignore.
 */
export function parseStreamLine(line: string): StreamEvent | null {
  const trimmed = line.trim();
  if (!trimmed.startsWith("{")) return null;
  let obj: unknown;
  try {
    obj = JSON.parse(trimmed);
  } catch {
    return null;
  }
  if (!isObject(obj)) return null;

  if (obj.type === "assistant") {
    const message = isObject(obj.message) ? obj.message : {};
    const content = message.content;
    const texts: string[] = [];
    const tools: ToolUse[] = [];
    if (typeof content === "string") {
      if (content) texts.push(content);
    } else if (Array.isArray(content)) {
      for (const block of content) {
        if (!isObject(block)) continue;
        if (block.type === "text" && typeof block.text === "string" && block.text.length > 0) {
          texts.push(block.text);
        } else if (block.type === "tool_use" && typeof block.name === "string") {
          tools.push(describeTool(block.name, block.input));
        }
      }
    }
    const nested = typeof obj.parent_tool_use_id === "string" && obj.parent_tool_use_id.length > 0;
    return { type: "assistant", texts, tools, nested };
  }

  if (obj.type === "result") {
    const subtype = str(obj.subtype);
    const event: ResultEvent = {
      type: "result",
      ok: subtype === "success" && obj.is_error !== true,
      subtype,
      result: str(obj.result),
    };
    const sessionId = str(obj.session_id);
    if (sessionId) event.sessionId = sessionId;
    const cost = num(obj.total_cost_usd);
    if (cost !== undefined) event.costUsd = cost;
    const duration = num(obj.duration_ms);
    if (duration !== undefined) event.durationMs = duration;
    return event;
  }

  return null;
}

const MAX_DETAIL = 48;

function shorten(s: string, max = MAX_DETAIL): string {
  const oneLine = s.replace(/\s+/g, " ").trim();
  return oneLine.length > max ? `${oneLine.slice(0, max - 1)}…` : oneLine;
}

function basename(p: string): string {
  const parts = p.split(/[\\/]/);
  return parts[parts.length - 1] || p;
}

/** Builds a compact chip label for a tool_use block. Never includes file contents. */
export function describeTool(name: string, input: unknown): ToolUse {
  const args = isObject(input) ? input : {};
  let label = name;
  let detail = "";
  const mcp = /^mcp__(.+?)__(.+)$/.exec(name);
  if (mcp) {
    label = `${mcp[2]} (${mcp[1].replace(/^claude_ai_/, "")})`;
  } else {
    switch (name) {
      case "Read":
      case "Write":
      case "Edit":
      case "MultiEdit":
      case "NotebookEdit":
      case "NotebookRead":
        detail = basename(str(args.file_path) || str(args.notebook_path) || str(args.path));
        break;
      case "Glob":
      case "Grep":
        detail = str(args.pattern);
        break;
      case "Bash":
        detail = str(args.description) || str(args.command);
        break;
      case "WebFetch": {
        const url = str(args.url);
        const host = /^[a-z][a-z0-9+.-]*:\/\/([^/?#]+)/i.exec(url);
        detail = host ? host[1] : url;
        break;
      }
      case "WebSearch":
        detail = str(args.query);
        break;
      case "Task":
      case "Agent":
        detail = str(args.description);
        break;
      default:
        break;
    }
  }
  return { name, label: shorten(label, 40), detail: shorten(detail) };
}

/**
 * Extracts the single JSON object printed by the one-shot modes. Tolerates
 * stray log lines around it. Returns undefined when nothing parses.
 */
export function parseJsonOutput(stdout: string): unknown {
  const text = stdout.trim();
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    // fall through
  }
  const lines = text.split(/\r?\n/);
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i].trim();
    if (!line.startsWith("{")) continue;
    try {
      return JSON.parse(line);
    } catch {
      // keep looking
    }
  }
  const first = text.indexOf("{");
  const last = text.lastIndexOf("}");
  if (first !== -1 && last > first) {
    try {
      return JSON.parse(text.slice(first, last + 1));
    } catch {
      // give up
    }
  }
  return undefined;
}

function throwIfError(v: unknown): Json {
  if (!isObject(v)) throw new Error("me returned something other than a JSON object");
  if (typeof v.error === "string" && v.error) throw new Error(v.error);
  return v;
}

function uniqueStrings(values: unknown): string[] {
  if (!Array.isArray(values)) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const v of values) {
    if (typeof v !== "string") continue;
    const t = v.trim();
    if (!t || seen.has(t)) continue;
    seen.add(t);
    out.push(t);
  }
  return out;
}

export interface LinkSuggestion {
  phrase: string;
  target: string;
}

export interface LinksResult {
  links: LinkSuggestion[];
  tags: string[];
}

export function parseLinksResult(v: unknown): LinksResult {
  const obj = throwIfError(v);
  if (!Array.isArray(obj.links) && !Array.isArray(obj.tags)) {
    throw new Error("me returned JSON without \"links\" or \"tags\"");
  }
  const links: LinkSuggestion[] = [];
  const seen = new Set<string>();
  for (const item of Array.isArray(obj.links) ? obj.links : []) {
    if (!isObject(item)) continue;
    const phrase = str(item.phrase).trim();
    const target = str(item.target).trim();
    if (!phrase || !target || seen.has(phrase)) continue;
    seen.add(phrase);
    links.push({ phrase, target });
  }
  return { links, tags: uniqueStrings(obj.tags) };
}

export function parseTasksResult(v: unknown): { tasks: string[] } {
  const obj = throwIfError(v);
  if (!Array.isArray(obj.tasks)) throw new Error("me returned JSON without a \"tasks\" list");
  return { tasks: uniqueStrings(obj.tasks) };
}

export function parseSummaryResult(v: unknown): { summary: string } {
  const obj = throwIfError(v);
  const summary = str(obj.summary).trim();
  if (!summary) throw new Error("me returned an empty summary");
  return { summary };
}

/** The outcome of one `me` process, as seen by the failure formatter. */
export interface ProcessOutcome {
  code: number | null;
  signal: string | null;
  stdout: string;
  stderr: string;
  spawnError?: string;
}

const MAX_ERROR = 4000;

/** Turns a failed run into the message shown in the UI: spawn error, JSON error, stderr, or exit code. */
export function failureMessage(r: ProcessOutcome): string {
  if (r.spawnError) return r.spawnError;
  const parsed = parseJsonOutput(r.stdout);
  if (isObject(parsed) && typeof parsed.error === "string" && parsed.error.trim()) {
    return parsed.error.trim();
  }
  const err = r.stderr.trim();
  if (err) return err.length > MAX_ERROR ? `…${err.slice(-MAX_ERROR)}` : err;
  if (r.signal) return `me was stopped (${r.signal})`;
  return `me exited with code ${r.code ?? "unknown"} and no error output`;
}

/** Positional text for argparse: guard values that start with "-" behind "--". */
export function positional(text: string): string[] {
  return text.startsWith("-") ? ["--", text] : [text];
}
