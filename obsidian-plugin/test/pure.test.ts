/**
 * Tiny test runner for the pure modules. Run with `npm test`
 * (compiles with tsc into .test-build, then runs under plain Node).
 */
import assert from "node:assert/strict";
import { diaryCallout, diarySectionName, formatCost, hhmm, todayISO } from "../src/format";
import { applyLinks, findFirstEligible, formatWikilink, mergeTags, normalizeTag, protectedRanges, sanitizeTarget } from "../src/links";
import {
  LineBuffer,
  describeTool,
  failureMessage,
  parseJsonOutput,
  parseLinksResult,
  parseStreamLine,
  parseSummaryResult,
  parseTasksResult,
  positional,
  type StreamEvent,
} from "../src/parse";

let passed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
    console.log(`  ok    ${name}`);
  } catch (e) {
    failures.push(name);
    console.log(`  FAIL  ${name}\n        ${(e as Error).message.split("\n").join("\n        ")}`);
  }
}

const link = (phrase: string, target: string) => ({ phrase, target });

console.log("LineBuffer");

test("joins a line split across chunks", () => {
  const buf = new LineBuffer();
  assert.deepEqual(buf.push('{"type":"assi'), []);
  assert.deepEqual(buf.push('stant"}\n{"type"'), ['{"type":"assistant"}']);
  assert.deepEqual(buf.push(':"result"}\n'), ['{"type":"result"}']);
  assert.deepEqual(buf.flush(), []);
});

test("handles several lines per chunk, CRLF and empty chunks", () => {
  const buf = new LineBuffer();
  assert.deepEqual(buf.push("a\r\nb\n\nc"), ["a", "b", ""]);
  assert.deepEqual(buf.push(""), []);
  assert.deepEqual(buf.push("\r"), []);
  assert.deepEqual(buf.push("\n"), ["c"]);
});

test("flush returns an unterminated last line once", () => {
  const buf = new LineBuffer();
  buf.push('{"type":"result"}');
  assert.deepEqual(buf.flush(), ['{"type":"result"}']);
  assert.deepEqual(buf.flush(), []);
});

console.log("parseStreamLine");

test("assistant event yields text and tool chips", () => {
  const line = JSON.stringify({
    type: "assistant",
    message: {
      content: [
        { type: "text", text: "Reading the note." },
        { type: "tool_use", name: "Read", input: { file_path: "/Users/x/vault/Projects/Q4 plan.md" } },
        { type: "tool_use", name: "mcp__claude_ai_Atlassian__searchJiraIssuesUsingJql", input: { jql: "x" } },
      ],
    },
    parent_tool_use_id: null,
  });
  const ev = parseStreamLine(line);
  assert.ok(ev && ev.type === "assistant");
  assert.deepEqual(ev.texts, ["Reading the note."]);
  assert.equal(ev.nested, false);
  assert.deepEqual(
    ev.tools.map((t) => [t.label, t.detail]),
    [
      ["Read", "Q4 plan.md"],
      ["searchJiraIssuesUsingJql (Atlassian)", ""],
    ],
  );
});

test("sub-agent messages are flagged nested", () => {
  const ev = parseStreamLine(JSON.stringify({ type: "assistant", message: { content: [{ type: "text", text: "x" }] }, parent_tool_use_id: "toolu_1" }));
  assert.ok(ev && ev.type === "assistant" && ev.nested);
});

test("result event carries session id, cost and duration", () => {
  const ev = parseStreamLine(
    '{"type":"result","subtype":"success","is_error":false,"result":"Done.","session_id":"abc-123","total_cost_usd":0.0123,"duration_ms":4200}',
  );
  assert.deepEqual(ev, { type: "result", ok: true, subtype: "success", result: "Done.", sessionId: "abc-123", costUsd: 0.0123, durationMs: 4200 });
});

test("error result is not ok", () => {
  const ev = parseStreamLine('{"type":"result","subtype":"error_max_turns","is_error":true,"session_id":"s"}');
  assert.ok(ev && ev.type === "result" && !ev.ok && ev.subtype === "error_max_turns" && ev.sessionId === "s");
  const flagged = parseStreamLine('{"type":"result","subtype":"success","is_error":true,"result":"API error"}');
  assert.ok(flagged && flagged.type === "result" && !flagged.ok);
});

test("ignores other event types, blanks and non-JSON noise", () => {
  assert.equal(parseStreamLine('{"type":"system","subtype":"init","session_id":"s"}'), null);
  assert.equal(parseStreamLine('{"type":"user","message":{"content":[{"type":"tool_result","content":"secret note text"}]}}'), null);
  assert.equal(parseStreamLine(""), null);
  assert.equal(parseStreamLine("   "), null);
  assert.equal(parseStreamLine("Warning: something"), null);
  assert.equal(parseStreamLine('{"type":"assistant"'), null);
  assert.equal(parseStreamLine("[1,2]"), null);
});

test("a chunked stream reassembles into the expected events", () => {
  const stream =
    [
      '{"type":"system","subtype":"init","session_id":"s1"}',
      '{"type":"assistant","message":{"content":[{"type":"text","text":"Hé, "}]}}',
      '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Grep","input":{"pattern":"TODO"}}]}}',
      '{"type":"assistant","message":{"content":[{"type":"text","text":"found 3 — done"}]}}',
      '{"type":"result","subtype":"success","result":"found 3 — done","session_id":"s1","total_cost_usd":0.02}',
    ].join("\n") + "\n";
  const buf = new LineBuffer();
  const events: StreamEvent[] = [];
  for (let i = 0; i < stream.length; i += 7) {
    for (const line of buf.push(stream.slice(i, i + 7))) {
      const ev = parseStreamLine(line);
      if (ev) events.push(ev);
    }
  }
  buf.flush().forEach((l) => {
    const ev = parseStreamLine(l);
    if (ev) events.push(ev);
  });
  assert.deepEqual(
    events.map((e) => e.type),
    ["assistant", "assistant", "assistant", "result"],
  );
  const texts = events.flatMap((e) => (e.type === "assistant" ? e.texts : []));
  assert.deepEqual(texts, ["Hé, ", "found 3 — done"]);
  const last = events[3];
  assert.ok(last.type === "result" && last.sessionId === "s1" && last.costUsd === 0.02);
});

test("describeTool keeps details short and content-free", () => {
  assert.deepEqual(describeTool("Bash", { command: "ls -la", description: "List files" }), { name: "Bash", label: "Bash", detail: "List files" });
  assert.equal(describeTool("WebFetch", { url: "https://example.com/a?b=c" }).detail, "example.com");
  assert.equal(describeTool("Write", { file_path: "/a/b.md", content: "SECRET" }).detail, "b.md");
  assert.ok(describeTool("Grep", { pattern: "x".repeat(200) }).detail.length <= 48);
});

console.log("one-shot JSON");

test("parseJsonOutput tolerates log lines", () => {
  assert.deepEqual(parseJsonOutput('{"tasks":["a"]}\n'), { tasks: ["a"] });
  assert.deepEqual(parseJsonOutput('loading…\n{"summary":"s"}\n'), { summary: "s" });
  assert.equal(parseJsonOutput(""), undefined);
  assert.equal(parseJsonOutput("not json"), undefined);
});

test("result parsers validate shape and surface {error}", () => {
  assert.deepEqual(parseTasksResult({ tasks: [" Email Ravi ", "", "Email Ravi", 3] }), { tasks: ["Email Ravi"] });
  assert.deepEqual(parseLinksResult({ links: [{ phrase: "Q4", target: "Q4 Plan" }, { phrase: "Q4", target: "Other" }, { phrase: "", target: "x" }], tags: ["#a", "#a"] }), {
    links: [{ phrase: "Q4", target: "Q4 Plan" }],
    tags: ["#a"],
  });
  assert.deepEqual(parseSummaryResult({ summary: " s " }), { summary: "s" });
  assert.throws(() => parseTasksResult({ error: "rate limited" }), /rate limited/);
  assert.throws(() => parseTasksResult({}), /tasks/);
  assert.throws(() => parseSummaryResult({ summary: "" }), /empty/);
  assert.throws(() => parseLinksResult([]), /JSON object/);
});

test("failureMessage prefers spawn error, then JSON error, then stderr, then exit code", () => {
  const base = { code: 1, signal: null, stdout: "", stderr: "" };
  assert.equal(failureMessage({ ...base, spawnError: "ENOENT" }), "ENOENT");
  assert.equal(failureMessage({ ...base, stdout: '{"error":"no note"}', stderr: "trace" }), "no note");
  assert.equal(failureMessage({ ...base, stderr: "  boom\n" }), "boom");
  assert.equal(failureMessage({ ...base, code: 2 }), "me exited with code 2 and no error output");
  assert.equal(failureMessage({ ...base, code: null, signal: "SIGTERM" }), "me was stopped (SIGTERM)");
});

test("positional guards leading dashes", () => {
  assert.deepEqual(positional("hello"), ["hello"]);
  assert.deepEqual(positional("-v please"), ["--", "-v please"]);
});

console.log("link insertion");

test("links only the first occurrence", () => {
  const r = applyLinks("Ravi said hi. Later Ravi left.", [link("Ravi", "Ravi Kumar")]);
  assert.equal(r.text, "[[Ravi Kumar|Ravi]] said hi. Later Ravi left.");
  assert.equal(r.applied.length, 1);
});

test("uses [[target]] when phrase equals target, alias form otherwise", () => {
  assert.equal(applyLinks("See Project X now.", [link("Project X", "Project X")]).text, "See [[Project X]] now.");
  assert.equal(applyLinks("See the plan now.", [link("the plan", "Q4 Plan")]).text, "See [[Q4 Plan|the plan]] now.");
  assert.equal(formatWikilink("A", "a"), "[[A|a]]");
});

test("skips occurrences already inside [[…]] (including aliases and embeds)", () => {
  const text = "[[Ravi]] and [[People/Ravi|Ravi]] and ![[Ravi]] then Ravi again.";
  assert.equal(applyLinks(text, [link("Ravi", "Ravi")]).text, "[[Ravi]] and [[People/Ravi|Ravi]] and ![[Ravi]] then [[Ravi]] again.");
});

test("never touches fenced code blocks (``` and ~~~, in callouts too)", () => {
  const text = ["```js", "const Ravi = 1;", "```", "~~~~", "Ravi ~~~", "~~~~", "> ```", "> Ravi", "> ```", "Ravi is here."].join("\n");
  const r = applyLinks(text, [link("Ravi", "Ravi Kumar")]);
  assert.equal(r.text, text.replace("Ravi is here.", "[[Ravi Kumar|Ravi]] is here."));
});

test("an unclosed fence protects the rest of the note", () => {
  const r = applyLinks("```\nRavi\nRavi", [link("Ravi", "Ravi Kumar")]);
  assert.equal(r.text, "```\nRavi\nRavi");
  assert.equal(r.skipped.length, 1);
});

test("skips inline code, frontmatter, markdown links, URLs, tags and marker regions", () => {
  const text = [
    "---",
    "title: Ravi",
    "tags: [Ravi]",
    "---",
    "`Ravi` [Ravi](https://x.y/Ravi) https://ravi.example/Ravi #Ravi",
    "<!-- me:brief -->",
    "Ravi in a marker",
    "<!-- /me:brief -->",
    "Finally Ravi.",
  ].join("\n");
  const r = applyLinks(text, [link("Ravi", "Ravi Kumar")]);
  assert.equal(r.text, text.replace("Finally Ravi.", "Finally [[Ravi Kumar|Ravi]]."));
});

test("respects word boundaries", () => {
  const r = applyLinks("Ravindra met Ravi.", [link("Ravi", "Ravi Kumar")]);
  assert.equal(r.text, "Ravindra met [[Ravi Kumar|Ravi]].");
});

test("falls back to a case-insensitive match and keeps the note's wording", () => {
  const r = applyLinks("the q4 plan is late", [link("Q4 plan", "Q4 Plan")]);
  assert.equal(r.text, "the [[Q4 Plan|q4 plan]] is late");
});

test("prefers longer phrases and never nests links", () => {
  const r = applyLinks("Email the Q4 draft. Q4 matters.", [link("Q4", "Q4 OKRs"), link("Q4 draft", "Q4 Draft")]);
  assert.equal(r.text, "Email the [[Q4 Draft|Q4 draft]]. [[Q4 OKRs|Q4]] matters.");
});

test("escapes the alias pipe inside tables", () => {
  const r = applyLinks("| who | note |\n| --- | --- |\n| Ravi | ok |", [link("Ravi", "Ravi Kumar")]);
  assert.equal(r.text, "| who | note |\n| --- | --- |\n| [[Ravi Kumar\\|Ravi]] | ok |");
});

test("reports phrases it can't place and unusable targets", () => {
  const r = applyLinks("Nothing here.", [link("Ravi", "Ravi"), link("here", "a|b|c"), link("x\ny", "Z")]);
  assert.equal(r.text, "Nothing [[a|here]].");
  assert.deepEqual(r.skipped.map((l) => l.phrase).sort(), ["Ravi", "x\ny"]);
  assert.equal(sanitizeTarget("[[Notes/Ravi.md]]"), "Notes/Ravi");
  assert.equal(sanitizeTarget("  "), null);
});

test("findFirstEligible and protectedRanges agree", () => {
  const text = "`Ravi` Ravi";
  assert.deepEqual(protectedRanges(text), [[0, 6]]);
  assert.deepEqual(findFirstEligible(text, "Ravi"), { index: 7, matched: "Ravi" });
  assert.equal(findFirstEligible(text, "Bob"), null);
});

console.log("tags");

test("normalizeTag", () => {
  assert.equal(normalizeTag("#project/q4"), "project/q4");
  assert.equal(normalizeTag("deep work"), "deep-work");
  assert.equal(normalizeTag("#2024"), null);
  assert.equal(normalizeTag("bad!tag"), null);
});

test("mergeTags keeps existing entries and dedupes case-insensitively", () => {
  assert.deepEqual(mergeTags(["Work"], ["#work", "#q4"]), { tags: ["Work", "q4"], added: ["q4"] });
  assert.deepEqual(mergeTags("a, b", ["#c"]), { tags: ["a", "b", "c"], added: ["c"] });
  assert.deepEqual(mergeTags(undefined, ["#x", "x"]), { tags: ["x"], added: ["x"] });
  assert.deepEqual(mergeTags(null, ["#legacy"], "legacy"), { tags: [], added: [] });
});

console.log("format");

test("dates, diary callout and cost", () => {
  const d = new Date(2026, 9, 5, 9, 7);
  assert.equal(todayISO(d), "2026-10-05");
  assert.equal(hhmm(d), "0907");
  assert.equal(diarySectionName(d), "note-0907");
  assert.match(diarySectionName(d), /^[a-z0-9][a-z0-9-]*$/);
  assert.equal(diaryCallout("Q4 Plan", "Line one.\n\nLine two."), "> [!note] From [[Q4 Plan]]\n> Line one.\n>\n> Line two.\n");
  assert.equal(formatCost(0.0042), "$0.0042");
  assert.equal(formatCost(0.123), "$0.123");
});

console.log(`\n${passed} passed, ${failures.length} failed`);
if (failures.length > 0) process.exit(1);
