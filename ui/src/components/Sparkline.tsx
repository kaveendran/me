import { useEffect, useId, useRef, useState, type KeyboardEvent, type PointerEvent, type RefObject } from "react";
import { shortDate } from "../format";

export interface Point {
  date: string; // YYYY-MM-DD
  value: number | null;
}

interface Props {
  title: string;
  /** Axis label for the y scale, e.g. "1–5" or "hours". */
  yLabel: string;
  points: Point[];
  domain: [number, number];
  format: (v: number) => string;
  /** Optional note under the chart. */
  note?: string;
}

const H = 120;
const PAD = { top: 10, right: 10, bottom: 22, left: 30 };

function useWidth<T extends HTMLElement>(): [RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(280);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const cw = entries[0]?.contentRect.width;
      if (cw && Math.abs(cw - w) > 0.5) setW(cw);
    });
    ro.observe(el);
    setW(el.clientWidth || 280);
    return () => ro.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return [ref, w];
}

/** Single-series 14-day line, inline SVG. Hover, tap or arrow keys read a day's value. */
export function Sparkline({ title, yLabel, points, domain, format, note }: Props) {
  const [wrapRef, width] = useWidth<HTMLDivElement>();
  const [active, setActive] = useState<number | null>(null);
  const labelId = useId();

  const n = points.length;
  const [lo, hi] = domain;
  const innerW = Math.max(40, width - PAD.left - PAD.right);
  const innerH = H - PAD.top - PAD.bottom;
  const x = (i: number) => PAD.left + (n <= 1 ? innerW / 2 : (i / (n - 1)) * innerW);
  const y = (v: number) => PAD.top + innerH - ((Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo || 1)) * innerH;

  // Break the line at missing days; each run also gets a soft area under it.
  const segments: string[] = [];
  const areas: string[] = [];
  const base = (PAD.top + innerH).toFixed(1);
  let cur = "";
  let first = -1;
  let prev = -1;
  const close = () => {
    if (!cur) return;
    segments.push(cur);
    if (prev > first) areas.push(`${cur}L${x(prev).toFixed(1)},${base}L${x(first).toFixed(1)},${base}Z`);
  };
  points.forEach((p, i) => {
    if (p.value == null) {
      close();
      cur = "";
      return;
    }
    if (!cur) first = i;
    prev = i;
    cur += `${cur ? "L" : "M"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`;
  });
  close();
  const gradId = `${labelId.replace(/[^a-zA-Z0-9_-]/g, "")}-area`;

  const values = points.map((p) => p.value).filter((v): v is number => v != null);
  const avg = values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
  let lastIdx = -1;
  for (let i = n - 1; i >= 0; i--) {
    if (points[i]?.value != null) {
      lastIdx = i;
      break;
    }
  }
  const last = lastIdx >= 0 ? (points[lastIdx]?.value ?? null) : null;

  const mid = (lo + hi) / 2;
  const ticks = [hi, mid, lo];

  const pick = (clientX: number) => {
    const el = wrapRef.current;
    if (!el || n === 0) return;
    const rect = el.getBoundingClientRect();
    const px = clientX - rect.left;
    const i = n <= 1 ? 0 : Math.round(((px - PAD.left) / innerW) * (n - 1));
    setActive(Math.max(0, Math.min(n - 1, i)));
  };

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (n === 0) return;
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      const d = e.key === "ArrowRight" ? 1 : -1;
      setActive((a) => Math.max(0, Math.min(n - 1, (a ?? n - 1) + (a == null ? 0 : d))));
    } else if (e.key === "Home") {
      setActive(0);
    } else if (e.key === "End") {
      setActive(n - 1);
    } else if (e.key === "Escape") {
      setActive(null);
    }
  };

  const ap = active != null ? points[active] : undefined;
  const activeText = ap
    ? `${shortDate(ap.date)}: ${ap.value == null ? "no data" : format(ap.value)}`
    : "";

  const tipLeft = active != null ? Math.min(Math.max(x(active), 60), width - 60) : 0;
  const firstDate = points[0]?.date;
  const lastDate = points[n - 1]?.date;

  return (
    <figure className="spark">
      <figcaption className="spark-head">
        <span className="spark-title" id={labelId}>
          {title}
        </span>
        <span className="spark-stats">
          {last != null ? (
            <>
              <strong>{format(last)}</strong> latest
            </>
          ) : (
            <span className="muted">no data yet</span>
          )}
          {avg != null ? <span className="muted"> · avg {format(avg)}</span> : null}
        </span>
      </figcaption>
      <div
        ref={wrapRef}
        className="spark-plot"
        tabIndex={0}
        role="group"
        aria-labelledby={labelId}
        aria-describedby={`${labelId}-help`}
        onKeyDown={onKey}
        onFocus={() => setActive((a) => a ?? (lastIdx >= 0 ? lastIdx : n - 1))}
        onBlur={() => setActive(null)}
        onPointerMove={(e: PointerEvent<HTMLDivElement>) => pick(e.clientX)}
        onPointerDown={(e: PointerEvent<HTMLDivElement>) => pick(e.clientX)}
        onPointerLeave={(e: PointerEvent<HTMLDivElement>) => e.pointerType === "mouse" && setActive(null)}
      >
        <span id={`${labelId}-help`} className="sr-only">
          Last {n} days, y axis {yLabel}. Use left and right arrow keys to read each day.
        </span>
        <svg width={width} height={H} aria-hidden="true" focusable="false">
          {/* y axis grid + tick labels */}
          <defs>
            <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" className="spark-area-top" />
              <stop offset="1" className="spark-area-bottom" />
            </linearGradient>
          </defs>
          {ticks.map((t, i) => (
            <g key={i}>
              <line
                className={`spark-grid ${i === ticks.length - 1 ? "is-base" : ""}`}
                x1={PAD.left}
                x2={PAD.left + innerW}
                y1={y(t)}
                y2={y(t)}
              />
              <text className="spark-tick" x={PAD.left - 6} y={y(t)} dy="0.32em" textAnchor="end">
                {format(t)}
              </text>
            </g>
          ))}
          {/* x axis labels */}
          {firstDate ? (
            <text className="spark-tick" x={x(0)} y={H - 6} textAnchor="start">
              {shortDate(firstDate, { day: "numeric", month: "short" })}
            </text>
          ) : null}
          {lastDate && n > 1 ? (
            <text className="spark-tick" x={x(n - 1)} y={H - 6} textAnchor="end">
              {shortDate(lastDate, { day: "numeric", month: "short" })}
            </text>
          ) : null}
          {active != null ? (
            <line className="spark-cross" x1={x(active)} x2={x(active)} y1={PAD.top} y2={PAD.top + innerH} />
          ) : null}
          {areas.map((d, i) => (
            <path key={`a${i}`} d={d} fill={`url(#${gradId})`} />
          ))}
          {segments.map((d, i) => (
            <path key={i} className="spark-line" d={d} />
          ))}
          {points.map((p, i) =>
            p.value == null ? null : (
              <circle
                key={i}
                className={`spark-dot ${i === lastIdx ? "is-last" : ""} ${i === active ? "is-active" : ""}`}
                cx={x(i)}
                cy={y(p.value)}
                r={i === active ? 4.5 : i === lastIdx ? 3.5 : 2.25}
              />
            ),
          )}
        </svg>
        {ap ? (
          <div className="spark-tip" style={{ left: tipLeft }} aria-hidden="true">
            <span className="muted">{shortDate(ap.date)}</span>
            <strong>{ap.value == null ? "no data" : format(ap.value)}</strong>
          </div>
        ) : null}
        <span className="sr-only" aria-live="polite">
          {activeText}
        </span>
      </div>
      {note ? <p className="spark-note muted">{note}</p> : null}
      <table className="sr-only">
        <caption>{title}</caption>
        <thead>
          <tr>
            <th scope="col">Date</th>
            <th scope="col">{yLabel}</th>
          </tr>
        </thead>
        <tbody>
          {points.map((p) => (
            <tr key={p.date}>
              <td>{shortDate(p.date)}</td>
              <td>{p.value == null ? "no data" : format(p.value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
