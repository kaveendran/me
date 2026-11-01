import { useEffect, useId, useRef, useState } from "react";
import type { ThemeSnapshot } from "../api";
import { useTheme } from "../theme";
import { IconArrowRight } from "./Icons";

export type Tone = "accent" | "ok" | "warn" | "calm";

/** Token family for a state's dot when the palette isn't showing that state's own accent. */
const STATE_TONE: Record<string, Tone> = { flow: "ok", steady: "accent", busy: "warn", strained: "calm", rest: "calm" };

/**
 * The live state's dot colour. With adaptive on (and no preview), the accent *is*
 * the state's colour; otherwise fall back to a token that reads the same way.
 */
export function liveTone(s: ThemeSnapshot, previewing: boolean): Tone {
  if (s.prefs.adaptive && !previewing) return "accent";
  return STATE_TONE[s.state.id] ?? "calm";
}

export const capFirst = (t: string) => (t ? t[0]!.toUpperCase() + t.slice(1) : t);

export function StateDot({ tone }: { tone: Tone }) {
  return <span className={`state-dot tone-${tone}`} aria-hidden="true" />;
}

/** Sidebar pill: dot + state + load. Opens a popover with the message and reasons. */
export function StatePill({ compact, onAppearance }: { compact?: boolean; onAppearance: () => void }) {
  const { snapshot, previewId, error } = useTheme();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const id = useId();

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!snapshot) {
    return compact || error ? null : (
      <div className="state-pill is-loading" aria-hidden="true">
        <span className="skeleton skel-dot" />
        <span className="skeleton" style={{ width: "56%" }} />
      </div>
    );
  }

  const s = snapshot.state;
  const tone = liveTone(snapshot, !!previewId);
  const load = Math.round(s.load);
  const previewLabel = previewId ? snapshot.states.find((x) => x.id === previewId)?.label : null;

  return (
    <div className={`state ${compact ? "is-compact" : ""}`} ref={ref}>
      <button
        type="button"
        className="state-pill"
        aria-expanded={open}
        aria-controls={id}
        aria-label={`${s.label}, load ${load}. Show details`}
        onClick={() => setOpen((o) => !o)}
      >
        <StateDot tone={tone} />
        <span className="state-label">{s.label}</span>
        {compact ? null : <span className="state-load">load {load}</span>}
      </button>
      {open ? (
        <div className="popover state-pop" id={id} role="dialog" aria-label={`Right now: ${s.label}`}>
          <div className="state-pop-head">
            <StateDot tone={tone} />
            <strong>{s.label}</strong>
            <span className="state-pop-load">
              Load <b>{load}</b>/100
            </span>
          </div>
          <p className="state-pop-msg">{s.message}</p>
          {s.reasons.length ? (
            <>
              <div className="section-label">Why</div>
              <ul className="reasons">
                {s.reasons.map((r, i) => (
                  <li key={i}>{capFirst(r)}</li>
                ))}
              </ul>
            </>
          ) : null}
          <div className="state-pop-foot">
            <span className="muted small">
              {previewLabel
                ? `Previewing ${previewLabel}`
                : snapshot.prefs.adaptive
                  ? "Colours follow this state"
                  : "Adaptive colours are off"}
            </span>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => {
                setOpen(false);
                onAppearance();
              }}
            >
              Appearance <IconArrowRight size={13} />
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** Today: one quiet line when the live state isn't `steady`. */
export function StateBanner({ onAppearance }: { onAppearance: () => void }) {
  const { snapshot, previewId } = useTheme();
  if (!snapshot || snapshot.state.id === "steady") return null;
  const s = snapshot.state;
  const top = s.reasons[0];
  return (
    <div className={`state-banner state-${s.id}`} role="status">
      <StateDot tone={liveTone(snapshot, !!previewId)} />
      <p className="state-banner-text">
        <strong>{s.label}</strong>
        <span className="state-banner-msg">{s.message}</span>
        {top ? <span className="state-banner-why">{capFirst(top)}</span> : null}
      </p>
      <button type="button" className="link-btn state-banner-link" onClick={onAppearance}>
        Details
      </button>
    </div>
  );
}
