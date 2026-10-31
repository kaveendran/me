import { Fragment, type ReactNode } from "react";
import { ApiError, SERVER_DOWN_HINT } from "../api";
import { IconAlert, IconExternal, IconRefresh } from "./Icons";

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
    <div className="loading" aria-busy="true">
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton" style={{ width: `${88 - i * 14}%` }} />
      ))}
    </div>
  );
}

export function Empty({ children, icon }: { children: ReactNode; icon?: ReactNode }) {
  return (
    <div className="empty">
      {icon ? <span className="empty-icon">{icon}</span> : null}
      <div>{children}</div>
    </div>
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
