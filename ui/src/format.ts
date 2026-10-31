// Small, dependency-free formatting helpers.

/** "09:30" -> minutes since midnight, or null if unparseable. */
export function hmToMin(hm: string | null | undefined): number | null {
  if (!hm) return null;
  const m = /^(\d{1,2}):(\d{2})/.exec(hm.trim());
  if (!m) return null;
  const h = Number(m[1]);
  const mm = Number(m[2]);
  if (h > 24 || mm > 59) return null;
  return h * 60 + mm;
}

export function minToHm(min: number): string {
  const h = Math.floor(min / 60) % 24;
  const m = Math.round(min % 60);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

export function nowMin(d: Date = new Date()): number {
  return d.getHours() * 60 + d.getMinutes() + d.getSeconds() / 60;
}

/** Local calendar date as YYYY-MM-DD. */
export function localDate(d: Date = new Date()): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** 255 -> "4h 15m", 45 -> "45m". */
export function duration(min: number | null | undefined): string {
  if (min == null || !Number.isFinite(min)) return "–";
  const total = Math.max(0, Math.round(min));
  const h = Math.floor(total / 60);
  const m = total % 60;
  if (h === 0) return `${m}m`;
  if (m === 0) return `${h}h`;
  return `${h}h ${m}m`;
}

/** Countdown text for a number of seconds. */
export function countdown(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m >= 10) return `${m}m`;
  return `${m}:${String(ss).padStart(2, "0")}`;
}

export function parseTs(ts: string | null | undefined): Date | null {
  if (!ts) return null;
  const t = Date.parse(ts);
  return Number.isNaN(t) ? null : new Date(t);
}

/** "3m ago", "2h ago", "yesterday", "in 5m". */
export function relTime(ts: string | null | undefined, now: Date = new Date()): string {
  const d = parseTs(ts);
  if (!d) return "never";
  const diff = (now.getTime() - d.getTime()) / 1000;
  const future = diff < 0;
  const a = Math.abs(diff);
  let s: string;
  if (a < 45) return future ? "in a moment" : "just now";
  if (a < 3600) s = `${Math.round(a / 60)}m`;
  else if (a < 86400) s = `${Math.round(a / 3600)}h`;
  else if (a < 86400 * 2 && !future) return "yesterday";
  else if (a < 86400 * 30) s = `${Math.round(a / 86400)}d`;
  else return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  return future ? `in ${s}` : `${s} ago`;
}

export function absTime(ts: string | null | undefined): string {
  const d = parseTs(ts);
  if (!d) return "";
  return d.toLocaleString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function clockTime(ts: string | null | undefined): string {
  const d = parseTs(ts);
  if (!d) return "";
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

/** "2026-11-02" -> "Mon 2 Nov" (parsed as a local date). */
export function shortDate(iso: string, opts?: Intl.DateTimeFormatOptions): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return d.toLocaleDateString(undefined, opts ?? { weekday: "short", day: "numeric", month: "short" });
}

export function money(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "–";
  if (n > 0 && n < 0.01) return "<$0.01";
  return `$${n.toFixed(2)}`;
}

export function errText(e: unknown): string {
  if (typeof e === "string") return e;
  if (e instanceof Error) return e.message;
  if (e && typeof e === "object") {
    const o = e as Record<string, unknown>;
    const parts = [o.source, o.scout, o.name, o.error ?? o.message].filter(
      (x): x is string => typeof x === "string" && x.length > 0,
    );
    if (parts.length) return parts.join(": ");
    try {
      return JSON.stringify(e);
    } catch {
      return String(e);
    }
  }
  return String(e);
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}
