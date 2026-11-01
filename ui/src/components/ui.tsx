import { Fragment, type ReactNode } from "react";
import { ApiError, SERVER_DOWN_HINT } from "../api";
import { IconAlert, IconExternal, IconInbox, IconRefresh } from "./Icons";

export function Spinner({ size = 16, label }: { size?: number; label?: string }) {
  return (
    <span className="spinner" style={{ width: size, height: size }} role={label ? "status" : undefined}>
      {label ? <span className="sr-only">{label}</span> : null}
    </span>
  );
}

export function Card({
  title,
  action,
  children,
  className = "",
  id,
}: {
  title?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  id?: string;
}) {
  return (
    <section className={`card ${className}`} aria-labelledby={id && title ? `${id}-h` : undefined}>
      {title || action ? (
        <header className="card-head">
          {title ? (
            <h2 className="card-title" id={id ? `${id}-h` : undefined}>
              {title}
            </h2>
          ) : (
            <span />
          )}
          {action}
        </header>
      ) : null}
      {children}
    </section>
  );
}

export function Loading({ label = "Loading…", rows = 3 }: { label?: string; rows?: number }) {
  return (
    <div className="loading" aria-busy="true" role="status">
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skel-row">
          <span className="skeleton skel-dot" />
          <span className="skel-lines">
            <span className="skeleton" style={{ width: `${82 - ((i * 23) % 36)}%` }} />
            <span className="skeleton skel-sub" style={{ width: `${48 - ((i * 13) % 22)}%` }} />
          </span>
        </div>
      ))}
    </div>
  );
}

/** A card-shaped placeholder: a title bar and a few skeleton rows. */
export function SkeletonCard({ rows = 3, className = "" }: { rows?: number; className?: string }) {
  return (
    <section className={`card ${className}`} aria-busy="true">
      <header className="card-head">
        <span className="skeleton skel-title" />
      </header>
      <Loading rows={rows} />
    </section>
  );
}

/** Friendly empty state: an icon and one line (a second, muted line is fine). */
export function Empty({ children, icon }: { children: ReactNode; icon?: ReactNode }) {
  return (
    <div className="empty">
      <span className="empty-icon" aria-hidden="true">
        {icon ?? <IconInbox size={16} />}
      </span>
      <div className="empty-text">{children}</div>
    </div>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  disabled,
  id,
  describedBy,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  disabled?: boolean;
  id?: string;
  describedBy?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      id={id}
      className="switch"
      aria-checked={checked}
      aria-label={label}
      aria-describedby={describedBy}
      disabled={disabled}
      onClick={() => onChange(!checked)}
    >
      <span className="switch-thumb" aria-hidden="true" />
    </button>
  );
}

/** Renders text with `backtick` spans as <code>. */
export function Ticks({ text }: { text: string }) {
  const parts = text.split(/(`[^`]+`)/g);
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith("`") && p.endsWith("`") && p.length > 2 ? (
          <code key={i}>{p.slice(1, -1)}</code>
        ) : (
          <Fragment key={i}>{p}</Fragment>
        ),
      )}
    </>
  );
}

export function ErrorState({ error, onRetry, compact }: { error: Error; onRetry?: () => void; compact?: boolean }) {
  // The app-wide banner already explains how to start the server, so keep this one short.
  const isDown = error instanceof ApiError && error.isNetwork && error.message === SERVER_DOWN_HINT;
  return (
    <div className={`error-state ${compact ? "compact" : ""}`} role="alert">
      <IconAlert size={18} />
      <div className="error-body">
        <div>
          <Ticks text={isDown ? "Couldn't load this. The server isn't answering." : error.message || "Something went wrong."} />
        </div>
        {onRetry ? (
          <button type="button" className="btn btn-ghost btn-sm" onClick={onRetry}>
            <IconRefresh size={14} /> Try again
          </button>
        ) : null}
      </div>
    </div>
  );
}

const URL_RE = /(https?:\/\/[^\s<>"')\]]+)/g;

/** Plain text with URLs turned into links that open in a new tab. */
export function Linkify({ text }: { text: string }) {
  const parts = text.split(URL_RE);
  return (
    <>
      {parts.map((p, i) =>
        i % 2 === 1 ? (
          <a key={i} href={p} target="_blank" rel="noopener noreferrer">
            {p}
          </a>
        ) : (
          <Fragment key={i}>{p}</Fragment>
        ),
      )}
    </>
  );
}

export function ExtLink({ href, children, className }: { href: string; children: ReactNode; className?: string }) {
  if (!href || !/^https?:\/\//i.test(href)) return <span className={className}>{children}</span>;
  return (
    <a className={`ext ${className ?? ""}`} href={href} target="_blank" rel="noopener noreferrer">
      {children}
      <IconExternal size={12} className="ext-icon" />
    </a>
  );
}

export function RefreshButton({ onClick, busy, label = "Refresh" }: { onClick: () => void; busy?: boolean; label?: string }) {
  return (
    <button
      type="button"
      className={`btn btn-icon ${busy ? "is-spinning" : ""}`}
      onClick={onClick}
      aria-label={label}
      title={label}
      disabled={busy}
    >
      <IconRefresh size={16} />
    </button>
  );
}

export function PageHead({ title, sub, actions }: { title: string; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div className="page-head-text">
        <h1>{title}</h1>
        {sub ? <p className="page-sub">{sub}</p> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </div>
  );
}
