import { Fragment, useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { api, parseChatEvent, type ChatEvent, type NoteKind } from "../api";
import { money } from "../format";
import { IconSpark, IconX } from "./Icons";
import { Linkify, Spinner, Ticks } from "./ui";

export type BarMode = "ask" | "capture";

interface Turn {
  id: number;
  q: string;
  text: string;
  tools: string[];
  streaming: boolean;
  error: string | null;
  cost: number | null;
}

const KINDS: ReadonlyArray<{ v: NoteKind; label: string; hint: string }> = [
  { v: "note", label: "Note", hint: "A thought to keep" },
  { v: "todo", label: "Todo", hint: "Something to do" },
  { v: "idea", label: "Idea", hint: "Something to explore" },
];

/** Light rendering for assistant text: `code`, **bold**, and links. */
function RichText({ text }: { text: string }) {
  const parts = text.split(/(`[^`\n]+`|\*\*[^*\n]+\*\*)/g);
  return (
    <>
      {parts.map((p, i) => {
        if (p.length > 2 && p.startsWith("`") && p.endsWith("`")) return <code key={i}>{p.slice(1, -1)}</code>;
        if (p.length > 4 && p.startsWith("**") && p.endsWith("**")) return <strong key={i}>{p.slice(2, -2)}</strong>;
        return (
          <Fragment key={i}>
            <Linkify text={p} />
          </Fragment>
        );
      })}
    </>
  );
}

export function CommandBar({
  open,
  mode,
  onMode,
  onClose,
}: {
  open: boolean;
  mode: BarMode;
  onMode: (m: BarMode) => void;
  onClose: () => void;
}) {
  // --- Ask state (kept while the overlay is closed so follow-ups work) ---
  const [turns, setTurns] = useState<Turn[]>([]);
  const [session, setSession] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const esRef = useRef<EventSource | null>(null);
  const nextId = useRef(1);
  const logRef = useRef<HTMLDivElement>(null);

  // --- Capture state ---
  const [kind, setKind] = useState<NoteKind>("note");
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const [capErr, setCapErr] = useState<string | null>(null);

  const dialogRef = useRef<HTMLDivElement>(null);
  const askInput = useRef<HTMLInputElement>(null);
  const capInput = useRef<HTMLTextAreaElement>(null);
  const restoreFocus = useRef<HTMLElement | null>(null);

  const streaming = turns.some((t) => t.streaming);

  useEffect(() => () => esRef.current?.close(), []);

  // Focus management.
  useEffect(() => {
    if (open) {
      const ae = document.activeElement;
      if (ae instanceof HTMLElement && !dialogRef.current?.contains(ae)) restoreFocus.current = ae;
      const t = window.setTimeout(() => (mode === "ask" ? askInput.current : capInput.current)?.focus(), 10);
      return () => window.clearTimeout(t);
    }
    restoreFocus.current?.focus?.();
    restoreFocus.current = null;
    return undefined;
  }, [open, mode]);

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns]);

  const patchTurn = useCallback((id: number, f: (t: Turn) => Turn) => {
    setTurns((ts) => ts.map((t) => (t.id === id ? f(t) : t)));
  }, []);

  const ask = (question: string) => {
    const qq = question.trim();
    if (!qq || streaming) return;
    esRef.current?.close();
    const id = nextId.current++;
    setTurns((ts) => [...ts, { id, q: qq, text: "", tools: [], streaming: true, error: null, cost: null }]);
    setQ("");

    let es: EventSource;
    try {
      es = new EventSource(api.chatUrl(qq, session));
    } catch {
      patchTurn(id, (t) => ({ ...t, streaming: false, error: "Couldn't open the chat stream." }));
      return;
    }
    esRef.current = es;
    let finished = false;
    let gotAny = false;

    const finish = () => {
      finished = true;
      es.close();
      if (esRef.current === es) esRef.current = null;
    };

    const handle = (ev: ChatEvent) => {
      gotAny = true;
      switch (ev.type) {
        case "text":
          patchTurn(id, (t) => ({ ...t, text: t.text + ev.text }));
          break;
        case "tool":
          patchTurn(id, (t) => ({ ...t, tools: [...t.tools, ev.name] }));
          break;
        case "done":
          if (ev.session) setSession(ev.session);
          patchTurn(id, (t) => ({ ...t, streaming: false, cost: ev.cost ?? null }));
          finish();
          break;
        case "error":
          patchTurn(id, (t) => ({ ...t, streaming: false, error: ev.error }));
          finish();
          break;
      }
    };

    const onData = (e: MessageEvent<string>) => {
      const ev = parseChatEvent(e.data);
      if (ev) handle(ev);
    };

    es.onmessage = onData;
    // Tolerate servers that name their events after the type.
    es.addEventListener("text", onData as EventListener);
    es.addEventListener("tool", onData as EventListener);
    es.addEventListener("done", onData as EventListener);
    es.addEventListener("error", (e: Event) => {
      if (e instanceof MessageEvent && typeof e.data === "string") {
        onData(e as MessageEvent<string>);
        return;
      }
      if (finished) return;
      finish();
      patchTurn(id, (t) => ({
        ...t,
        streaming: false,
        error: gotAny
          ? t.text
            ? "The stream ended before the answer finished."
            : "The stream was interrupted."
          : "Couldn't reach the chat endpoint. Is `me serve` running?",
      }));
    });
  };

  const stop = () => {
    const es = esRef.current;
    if (!es) return;
    es.close();
    esRef.current = null;
    setTurns((ts) => ts.map((t) => (t.streaming ? { ...t, streaming: false, error: t.error ?? "Stopped." } : t)));
  };

  const newChat = () => {
    stop();
    setTurns([]);
    setSession(null);
    askInput.current?.focus();
  };

  const submitAsk = (e: FormEvent) => {
    e.preventDefault();
    ask(q);
  };

  const capture = async (e?: FormEvent) => {
    e?.preventDefault();
    const body = text.trim();
    if (!body || saving) return;
    setSaving(true);
    setCapErr(null);
    setSaved(null);
    try {
      const res = await api.note(kind, body);
      setSaved(res.path || "your inbox");
      setText("");
      capInput.current?.focus();
    } catch (err) {
      setCapErr(err instanceof Error ? err.message : "Couldn't save.");
    } finally {
      setSaving(false);
    }
  };

  const onDialogKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      onClose();
      return;
    }
    if (e.key !== "Tab") return;
    const root = dialogRef.current;
    if (!root) return;
    const f = Array.from(
      root.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
      ),
    );
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

  if (!open) return null;

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        className="cmdbar"
        role="dialog"
        aria-modal="true"
        aria-label="Command bar"
        ref={dialogRef}
        onKeyDown={onDialogKey}
      >
        <div className="cmd-top">
          <div className="seg" role="tablist" aria-label="Mode">
            <button
              type="button"
              role="tab"
              aria-selected={mode === "ask"}
              className={mode === "ask" ? "is-on" : ""}
              onClick={() => onMode("ask")}
            >
              Ask
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === "capture"}
              className={mode === "capture" ? "is-on" : ""}
              onClick={() => onMode("capture")}
            >
              Capture
            </button>
          </div>
          <span className="cmd-hint muted">
            <kbd>Esc</kbd> to close
          </span>
          <button type="button" className="btn btn-icon" onClick={onClose} aria-label="Close">
            <IconX size={16} />
          </button>
        </div>

        {mode === "ask" ? (
          <div className="cmd-body" role="tabpanel" aria-label="Ask">
            <div className="chat-log" ref={logRef} aria-live="polite">
              {turns.length === 0 ? (
                <div className="chat-empty">
                  <IconSpark size={22} />
                  <p>Ask about your day, your work, or what you've written down.</p>
                  <div className="chat-suggest">
                    {["What should I focus on next?", "What's blocking my PRs?", "Summarise my afternoon"].map((s) => (
                      <button key={s} type="button" className="chip-btn" onClick={() => ask(s)}>
                        {s}
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                turns.map((t) => (
                  <div key={t.id} className="turn">
                    <div className="turn-q">{t.q}</div>
                    <div className="turn-a">
                      {t.tools.length ? (
                        <div className="tool-chips" aria-label="Tools used">
                          {t.tools.map((name, i) => (
                            <span key={i} className="tool-chip">
                              {name}
                            </span>
                          ))}
                        </div>
                      ) : null}
                      {t.text ? (
                        <div className="turn-text">
                          <RichText text={t.text} />
                          {t.streaming ? <span className="caret" aria-hidden="true" /> : null}
                        </div>
                      ) : t.streaming ? (
                        <div className="turn-thinking muted">
                          <Spinner size={14} /> Thinking…
                        </div>
                      ) : null}
                      {t.error ? (
                        <div className="inline-error" role="alert">
                          <Ticks text={t.error} />
                        </div>
                      ) : null}
                      {t.cost != null ? <div className="turn-meta muted">{money(t.cost)}</div> : null}
                    </div>
                  </div>
                ))
              )}
            </div>
            <form className="cmd-input-row" onSubmit={submitAsk}>
              <label className="sr-only" htmlFor="cmd-ask">
                Ask a question
              </label>
              <input
                id="cmd-ask"
                ref={askInput}
                className="cmd-input"
                type="text"
                autoComplete="off"
                placeholder={session ? "Ask a follow-up…" : "Ask anything…"}
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
              {streaming ? (
                <button type="button" className="btn btn-ghost" onClick={stop}>
                  Stop
                </button>
              ) : (
                <button type="submit" className="btn btn-primary" disabled={!q.trim()}>
                  Ask
                </button>
              )}
            </form>
            <div className="cmd-foot muted">
              {session ? (
                <>
                  <span>Continuing this conversation</span>
                  <button type="button" className="link-btn" onClick={newChat}>
                    New conversation
                  </button>
                </>
              ) : (
                <span>Answers stream in as they're written.</span>
              )}
            </div>
          </div>
        ) : (
          <form className="cmd-body" role="tabpanel" aria-label="Capture" onSubmit={(e) => void capture(e)}>
            <div className="seg seg-sm" role="radiogroup" aria-label="Kind">
              {KINDS.map((k) => (
                <button
                  key={k.v}
                  type="button"
                  role="radio"
                  aria-checked={kind === k.v}
                  className={kind === k.v ? "is-on" : ""}
                  onClick={() => setKind(k.v)}
                  title={k.hint}
                >
                  {k.label}
                </button>
              ))}
            </div>
            <label className="sr-only" htmlFor="cmd-capture">
              {kind} text
            </label>
            <textarea
              id="cmd-capture"
              ref={capInput}
              className="cmd-textarea"
              rows={4}
              placeholder={kind === "todo" ? "What needs doing?" : kind === "idea" ? "What's the idea?" : "Write it down…"}
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                setSaved(null);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault();
                  void capture();
                }
              }}
            />
            <div className="cmd-input-row end">
              <span className="muted small">
                <kbd>⌘</kbd>/<kbd>Ctrl</kbd> + <kbd>Enter</kbd> to save
              </span>
              <button type="submit" className="btn btn-primary" disabled={!text.trim() || saving}>
                {saving ? <Spinner size={14} /> : null} Save {kind}
              </button>
            </div>
            {saved ? (
              <p className="inline-ok" role="status">
                Saved to <code>{saved}</code>
              </p>
            ) : null}
            {capErr ? (
              <p className="inline-error" role="alert">
                {capErr}
              </p>
            ) : null}
          </form>
        )}
      </div>
    </div>
  );
}
