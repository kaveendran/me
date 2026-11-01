import { useEffect, useId, useRef, useState } from "react";
import type { Load, LoadBand } from "../api";

export const BAND_LABEL: Record<LoadBand, string> = { ok: "normal", elevated: "elevated", high: "high" };

export function bandOf(load: Load | null | undefined): LoadBand {
  const b = load?.band;
  return b === "elevated" || b === "high" ? b : "ok";
}

/** Load score pill; reasons appear on hover (mouse) or tap (touch/keyboard). */
export function LoadBadge({ load }: { load: Load | null | undefined }) {
  const [hover, setHover] = useState(false);
  const [pinned, setPinned] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const id = useId();
  const open = hover || pinned;

  useEffect(() => {
    if (!pinned) return;
    const onDown = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setPinned(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setPinned(false);
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [pinned]);

  if (!load) return null;
  const band = bandOf(load);
  const reasons = load.reasons ?? [];

  return (
    <div
      className={`load-badge band-${band}`}
      ref={ref}
      onPointerEnter={(e) => e.pointerType === "mouse" && setHover(true)}
      onPointerLeave={(e) => e.pointerType === "mouse" && setHover(false)}
    >
      <button
        type="button"
        className="load-pill"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setPinned((p) => !p)}
      >
        <span className="band-dot" aria-hidden="true" />
        <span>Load</span>
        <strong>{BAND_LABEL[band]}</strong>
        <span className="load-score">{Math.round(load.score)}</span>
      </button>
      <div className="popover load-pop" id={id} role="tooltip" hidden={!open}>
        <div className="load-pop-title">
          Load score {Math.round(load.score)} / 100
        </div>
        {reasons.length ? (
          <ul>
            {reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        ) : (
          <p className="muted">No particular pressure points today.</p>
        )}
      </div>
    </div>
  );
}
