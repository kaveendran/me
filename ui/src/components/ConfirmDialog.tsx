import { useEffect, useId, useRef, type KeyboardEvent } from "react";
import { Ticks } from "./ui";

/** Small modal that asks before an action with a side effect. Focus starts on Cancel. */
export function ConfirmDialog({
  title,
  body,
  confirmLabel = "Continue",
  onConfirm,
  onCancel,
}: {
  title: string;
  body: string;
  confirmLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const bodyId = useId();

  useEffect(() => {
    const prev = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    cancelRef.current?.focus();
    return () => prev?.focus?.();
  }, []);

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      onCancel();
      return;
    }
    if (e.key !== "Tab") return;
    const f = Array.from(ref.current?.querySelectorAll<HTMLElement>("button:not([disabled])") ?? []);
    if (!f.length) return;
    const first = f[0]!;
    const last = f[f.length - 1]!;
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  return (
    <div className="overlay overlay-center" onMouseDown={(e) => e.target === e.currentTarget && onCancel()}>
      <div
        className="dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
        ref={ref}
        onKeyDown={onKey}
      >
        <h2 className="dialog-title" id={titleId}>
          {title}
        </h2>
        <p className="dialog-body" id={bodyId}>
          <Ticks text={body} />
        </p>
        <div className="dialog-actions">
          <button type="button" className="btn" ref={cancelRef} onClick={onCancel}>
            Cancel
          </button>
          <button type="button" className="btn btn-primary" onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
