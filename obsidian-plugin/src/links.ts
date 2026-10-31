/**
 * Pure link and tag helpers for "Suggest links & tags". No Obsidian imports,
 * so the unit tests can run them under plain Node.
 */
import type { LinkSuggestion } from "./parse";

/** Half-open [start, end) character range. */
export type Range = [number, number];

interface Line {
  start: number;
  /** End of the line's text, excluding the newline. */
  end: number;
  /** Start of the next line (after the newline), or text.length. */
  next: number;
}

function splitLines(text: string): Line[] {
  const lines: Line[] = [];
  let start = 0;
  while (start <= text.length) {
    const nl = text.indexOf("\n", start);
    if (nl === -1) {
      lines.push({ start, end: text.length, next: text.length });
      break;
    }
    lines.push({ start, end: nl, next: nl + 1 });
    start = nl + 1;
  }
  return lines;
}

const FENCE_OPEN = /^((?:[ \t]*>)*[ \t]*)(`{3,}|~{3,})(.*)$/;
const FENCE_CLOSE = /^(?:[ \t]*>)*[ \t]*(`{3,}|~{3,})[ \t]*$/;

/** Ranges for YAML frontmatter and fenced code blocks (``` or ~~~, also inside callouts). */
function blockRanges(text: string): Range[] {
  const lines = splitLines(text);
  const ranges: Range[] = [];
  let i = 0;

  const lineText = (l: Line) => text.slice(l.start, l.end).replace(/\r$/, "");

  if (lines.length > 1 && lineText(lines[0]).trimEnd() === "---") {
    for (let j = 1; j < lines.length; j++) {
      const t = lineText(lines[j]).trimEnd();
      if (t === "---" || t === "...") {
        ranges.push([0, lines[j].next]);
        i = j + 1;
        break;
      }
    }
  }

  for (; i < lines.length; i++) {
    const open = FENCE_OPEN.exec(lineText(lines[i]));
    if (!open) continue;
    const fence = open[2];
    // A backtick fence's info string may not contain backticks (CommonMark).
    if (fence[0] === "`" && open[3].includes("`")) continue;
    let end = text.length;
    let j = i + 1;
    for (; j < lines.length; j++) {
      const close = FENCE_CLOSE.exec(lineText(lines[j]));
      if (close && close[1][0] === fence[0] && close[1].length >= fence.length) {
        end = lines[j].next;
        break;
      }
    }
    ranges.push([lines[i].start, end]);
    i = j; // an unclosed fence runs to the end of the note
  }
  return ranges;
}

const INLINE_PATTERNS: RegExp[] = [
  // me harness marker regions: the CLI owns these.
  /<!--\s*me:([a-z0-9][a-z0-9-]*)\s*-->[\s\S]*?<!--\s*\/me:\1\s*-->/g,
  // HTML and Obsidian comments.
  /<!--[\s\S]*?-->/g,
  /%%[\s\S]*?%%/g,
  // Display math.
  /\$\$[\s\S]*?\$\$/g,
  // Inline code spans, not crossing a blank line.
  /(`+)(?!`)(?:(?!\n[ \t]*\n)[\s\S])*?[^`]\1(?!`)/g,
  // Wikilinks and embeds.
  /!?\[\[[^\n]*?\]\]/g,
  // Markdown links and images.
  /!?\[[^\]\n]*\]\([^)\n]*\)/g,
  // Bare URLs.
  /[a-zA-Z][a-zA-Z0-9+.-]*:\/\/[^\s<>)\]]+/g,
  // Tags.
  /(?<![\p{L}\p{N}_])#[\p{L}\p{N}_/-]+/gu,
];

function mask(text: string, ranges: Range[]): string {
  let out = "";
  let pos = 0;
  for (const [s, e] of ranges) {
    if (s < pos) continue;
    out += text.slice(pos, s) + text.slice(s, e).replace(/[^\n]/g, " ");
    pos = e;
  }
  return out + text.slice(pos);
}

function merge(ranges: Range[]): Range[] {
  const sorted = ranges.filter(([s, e]) => e > s).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const out: Range[] = [];
  for (const r of sorted) {
    const last = out[out.length - 1];
    if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1]);
    else out.push([r[0], r[1]]);
  }
  return out;
}

/**
 * Every region a link insertion must not touch: frontmatter, code fences,
 * inline code, existing wikilinks/embeds, Markdown links, URLs, tags,
 * comments, math and `<!-- me:x -->` marker regions.
 */
export function protectedRanges(text: string): Range[] {
  const blocks = merge(blockRanges(text));
  const masked = mask(text, blocks);
  const ranges: Range[] = [...blocks];
  for (const pattern of INLINE_PATTERNS) {
    pattern.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = pattern.exec(masked)) !== null) {
      ranges.push([m.index, m.index + m[0].length]);
      if (m[0].length === 0) pattern.lastIndex++;
    }
  }
  return merge(ranges);
}

const WORD = /[\p{L}\p{N}_]/u;

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function overlaps(ranges: Range[], start: number, end: number): boolean {
  for (const [s, e] of ranges) {
    if (s >= end) return false; // ranges are sorted
    if (e > start) return true;
  }
  return false;
}

/** A phrase can become link text only if it is a single line with no link syntax in it. */
export function isLinkablePhrase(phrase: string): boolean {
  return phrase.trim().length > 0 && !/[\n\r|]|\[\[|\]\]/.test(phrase);
}

function scan(text: string, phrase: string, ranges: Range[], ignoreCase: boolean): { index: number; matched: string } | null {
  const re = new RegExp(escapeRegExp(phrase), ignoreCase ? "giu" : "gu");
  const startsWord = WORD.test(phrase[0]);
  const endsWord = WORD.test(phrase[phrase.length - 1]);
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const start = m.index;
    const end = start + m[0].length;
    re.lastIndex = start + 1;
    if (startsWord && start > 0 && WORD.test(text[start - 1])) continue;
    if (endsWord && end < text.length && WORD.test(text[end])) continue;
    if (overlaps(ranges, start, end)) continue;
    return { index: start, matched: m[0] };
  }
  return null;
}

/**
 * Finds the first occurrence of `phrase` that sits outside every protected
 * region and on word boundaries. Exact case wins; a case-insensitive match is
 * the fallback.
 */
export function findFirstEligible(text: string, phrase: string): { index: number; matched: string } | null {
  if (!isLinkablePhrase(phrase)) return null;
  const ranges = protectedRanges(text);
  return scan(text, phrase, ranges, false) ?? scan(text, phrase, ranges, true);
}

/** Cleans a model-suggested target: strips [[ ]], aliases and a .md suffix. Null if unusable. */
export function sanitizeTarget(target: string): string | null {
  let t = target.trim();
  if (t.startsWith("[[") && t.endsWith("]]")) t = t.slice(2, -2).trim();
  const pipe = t.indexOf("|");
  if (pipe !== -1) t = t.slice(0, pipe).trim();
  if (t.toLowerCase().endsWith(".md")) t = t.slice(0, -3).trim();
  if (!t || /[[\]\n\r|]/.test(t)) return null;
  return t;
}

/** `[[target]]` when the phrase equals the target, else `[[target|phrase]]` (pipe escaped in tables). */
export function formatWikilink(target: string, phrase: string, inTable = false): string {
  if (phrase === target) return `[[${target}]]`;
  return `[[${target}${inTable ? "\\|" : "|"}${phrase}]]`;
}

export interface LinkApplyResult {
  text: string;
  applied: LinkSuggestion[];
  skipped: LinkSuggestion[];
}

/**
 * Turns the first eligible occurrence of each phrase into a wikilink. Longer
 * phrases go first so "Q4 draft" wins over "Q4". Duplicate phrases are applied
 * once. Protected regions are recomputed after each insertion, so a new link is
 * never nested inside another.
 */
export function applyLinks(text: string, links: LinkSuggestion[]): LinkApplyResult {
  const seen = new Set<string>();
  const unique = links.filter((l) => {
    if (seen.has(l.phrase)) return false;
    seen.add(l.phrase);
    return true;
  });
  const ordered = unique
    .map((link, i) => ({ link, i }))
    .sort((a, b) => b.link.phrase.length - a.link.phrase.length || a.i - b.i)
    .map((x) => x.link);

  let out = text;
  const applied: LinkSuggestion[] = [];
  const skipped: LinkSuggestion[] = [];
  for (const link of ordered) {
    const target = sanitizeTarget(link.target);
    const hit = target ? findFirstEligible(out, link.phrase) : null;
    if (!target || !hit) {
      skipped.push(link);
      continue;
    }
    const lineStart = out.lastIndexOf("\n", hit.index - 1) + 1;
    const inTable = /^[ \t]*(?:>[ \t]*)*\|/.test(out.slice(lineStart, hit.index + 1));
    const replacement = formatWikilink(target, hit.matched, inTable);
    out = out.slice(0, hit.index) + replacement + out.slice(hit.index + hit.matched.length);
    applied.push(link);
  }
  return { text: out, applied, skipped };
}

/** Normalises a suggested tag for frontmatter: no leading #, spaces to dashes, Obsidian tag characters only. */
export function normalizeTag(raw: string): string | null {
  const t = raw.trim().replace(/^#+/, "").trim().replace(/\s+/g, "-");
  if (!t || !/^[\p{L}\p{N}_/-]+$/u.test(t) || /^[0-9]+$/.test(t)) return null;
  return t;
}

/** Reads a frontmatter `tags` value (list, or comma/space separated string) as a list. */
export function frontmatterTags(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.filter((v): v is string | number => typeof v === "string" || typeof v === "number").map(String).filter((s) => s.trim());
  }
  if (typeof value === "string") return value.split(/[,\s]+/).filter(Boolean);
  return [];
}

function tagKey(tag: string): string {
  return tag.trim().replace(/^#+/, "").toLowerCase();
}

/**
 * Merges new tags into a frontmatter `tags` value. Existing entries are kept
 * as written; comparison ignores case and a leading #. `alsoPresent` (e.g. the
 * legacy `tag` key) only suppresses duplicates.
 */
export function mergeTags(existing: unknown, add: string[], alsoPresent?: unknown): { tags: string[]; added: string[] } {
  const tags = frontmatterTags(existing);
  const keys = new Set([...tags, ...frontmatterTags(alsoPresent)].map(tagKey));
  const added: string[] = [];
  for (const raw of add) {
    const t = normalizeTag(raw);
    if (!t || keys.has(tagKey(t))) continue;
    keys.add(tagKey(t));
    added.push(t);
  }
  return { tags: [...tags, ...added], added };
}
