import type { CalEvent, PlanBlock } from "../api";
import { hmToMin, minToHm, nowMin } from "../format";
import { ExtLink } from "./ui";

const PX_PER_MIN = 1.15;
const MIN_ITEM_PX = 24;

interface Placed<T> {
  item: T;
  start: number;
  end: number;
  col: number;
  cols: number;
}

/** Greedy column assignment for overlapping intervals. */
function layout<T>(items: Array<{ item: T; start: number; end: number }>): Placed<T>[] {
  const sorted = [...items].sort((a, b) => a.start - b.start || b.end - a.end);
  const out: Placed<T>[] = [];
  let cluster: Placed<T>[] = [];
  let clusterEnd = -Infinity;
  const flush = () => {
    const cols = cluster.reduce((m, p) => Math.max(m, p.col + 1), 1);
    cluster.forEach((p) => (p.cols = cols));
    out.push(...cluster);
    cluster = [];
  };
  for (const it of sorted) {
    // Short items still occupy at least MIN_ITEM_PX visually.
    const visualEnd = Math.max(it.end, it.start + MIN_ITEM_PX / PX_PER_MIN);
    if (it.start >= clusterEnd && cluster.length) {
      flush();
      clusterEnd = -Infinity;
    }
    const used = new Set(
      cluster
        .filter((p) => Math.max(p.end, p.start + MIN_ITEM_PX / PX_PER_MIN) > it.start)
        .map((p) => p.col),
    );
    let col = 0;
    while (used.has(col)) col++;
    cluster.push({ ...it, col, cols: 1 });
    clusterEnd = Math.max(clusterEnd, visualEnd);
  }
  if (cluster.length) flush();
  return out;
}

export function Timeline({
  events,
  blocks,
  now,
  isToday,
}: {
  events: CalEvent[];
  blocks: PlanBlock[];
  now: Date;
  isToday: boolean;
}) {
  const ev = events
    .map((e) => ({ item: e, start: hmToMin(e.start), end: hmToMin(e.end) }))
    .filter((e): e is { item: CalEvent; start: number; end: number } => e.start != null)
    .map((e) => ({ ...e, end: e.end != null && e.end > e.start ? e.end : e.start + 30 }));
  const bl = blocks
    .map((b) => ({ item: b, start: hmToMin(b.start), end: hmToMin(b.end) }))
    .filter((b): b is { item: PlanBlock; start: number; end: number } => b.start != null)
    .map((b) => ({ ...b, end: b.end != null && b.end > b.start ? b.end : b.start + 30 }));

  const nm = nowMin(now);
  const all = [...ev, ...bl];
  let lo = Math.min(8 * 60, ...all.map((x) => x.start));
  let hi = Math.max(18 * 60, ...all.map((x) => x.end));
  if (isToday) {
    lo = Math.min(lo, Math.max(6 * 60, nm - 30));
    hi = Math.max(hi, Math.min(24 * 60, nm + 30));
  }
  lo = Math.floor(lo / 60) * 60;
  hi = Math.min(24 * 60, Math.ceil(hi / 60) * 60);
  const height = (hi - lo) * PX_PER_MIN;
  const top = (m: number) => (m - lo) * PX_PER_MIN;
  const hours: number[] = [];
  for (let h = lo; h <= hi; h += 60) hours.push(h);

  const placedEvents = layout(ev);
  const hasBlocks = bl.length > 0;
  const showNow = isToday && nm >= lo && nm <= hi;

  return (
    <div className={`timeline ${hasBlocks ? "has-blocks" : ""}`}>
      {hasBlocks ? (
        <div className="tl-legend" aria-hidden="true">
          <span>
            <i className="lg lg-block" /> focus block
          </span>
          <span>
            <i className="lg lg-event" /> meeting
          </span>
        </div>
      ) : null}
      <div className="tl-body" style={{ height }}>
        {hours.map((h) => (
          <div key={h} className="tl-hour" style={{ top: top(h) }}>
            <span className="tl-hour-label">{minToHm(h)}</span>
          </div>
        ))}

        <ol className="tl-lane tl-lane-blocks" aria-label="Focus blocks">
          {bl.map((b, i) => {
            const past = isToday && b.end <= nm;
            return (
              <li
                key={`b${i}`}
                className={`tl-item tl-block ${past ? "is-past" : ""}`}
                style={{ top: top(b.start), height: Math.max(MIN_ITEM_PX, (b.end - b.start) * PX_PER_MIN - 2) }}
              >
                <span className="tl-time">
                  {b.item.start}
                  <span className="tl-end">–{b.item.end}</span>
                </span>
                <span className="tl-title">{b.item.task}</span>
              </li>
            );
          })}
        </ol>

        <ol className="tl-lane tl-lane-events" aria-label="Meetings">
          {placedEvents.map((p) => {
            const e = p.item;
            const past = isToday && p.end <= nm;
            const live = isToday && p.start <= nm && nm < p.end;
            const h = Math.max(MIN_ITEM_PX, (p.end - p.start) * PX_PER_MIN - 2);
            return (
              <li
                key={e.id || `${e.start}-${e.title}`}
                className={`tl-item tl-event ${past ? "is-past" : ""} ${live ? "is-live" : ""} ${h < 40 ? "is-short" : ""}`}
                style={{
                  top: top(p.start),
                  height: h,
                  left: `${(p.col / p.cols) * 100}%`,
                  width: `calc(${100 / p.cols}% - 4px)`,
                }}
                title={`${e.start}–${e.end} ${e.title}`}
              >
                <span className="tl-time">
                  {e.start}
                  <span className="tl-end">–{e.end}</span>
                </span>
                <span className="tl-title">{e.title}</span>
                {e.attendees > 1 && h >= 40 ? <span className="tl-meta">{e.attendees} people</span> : null}
                {e.meet_url && !past ? (
                  <ExtLink href={e.meet_url} className="tl-join">
                    Join
                  </ExtLink>
                ) : null}
              </li>
            );
          })}
        </ol>

        {showNow ? (
          <div className="tl-now" style={{ top: top(nm) }} aria-label={`Now, ${minToHm(nm)}`} role="img">
            <span className="tl-now-label">{minToHm(nm)}</span>
          </div>
        ) : null}
      </div>
    </div>
  );
}
