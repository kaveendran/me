/** Small pure formatting helpers (dates, diary body, cost). */

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/** Local date as YYYY-MM-DD. */
export function todayISO(d: Date = new Date()): string {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

/** Local time as HHMM. */
export function hhmm(d: Date = new Date()): string {
  return `${pad2(d.getHours())}${pad2(d.getMinutes())}`;
}

/** Diary section name for a note summary; matches `[a-z0-9][a-z0-9-]*`. */
export function diarySectionName(d: Date = new Date()): string {
  return `note-${hhmm(d)}`;
}

/** The callout piped into `me vault section diary …`. Every summary line stays inside the callout. */
export function diaryCallout(noteName: string, summary: string): string {
  const body = summary
    .trim()
    .split(/\r?\n/)
    .map((line) => (line.trim() ? `> ${line}` : ">"))
    .join("\n");
  return `> [!note] From [[${noteName}]]\n${body}\n`;
}

export function formatCost(usd: number): string {
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  if (usd < 1) return `$${usd.toFixed(3)}`;
  return `$${usd.toFixed(2)}`;
}

export function formatDuration(ms: number): string {
  const s = ms / 1000;
  return s < 60 ? `${s.toFixed(1)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}
