import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import {
  api,
  SETUP_GROUPS,
  type SetupAction,
  type SetupItem,
  type SetupNtfy,
  type SetupProfile,
  type SetupProfileBody,
  type SetupStatus,
  type SetupVault,
} from "../api";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { IconAlert, IconCheck, IconCopy, IconExternal, IconPhone, IconRefresh, IconTerminal, IconX } from "../components/Icons";
import { QrCode } from "../components/QrCode";
import { Card, Empty, ErrorState, Loading, PageHead, Spinner, Ticks } from "../components/ui";
import { useAppData } from "../data";
import { useToast } from "../toast";

const TERMINAL_POLL_MS = 5_000;
const TERMINAL_WAIT_MS = 2 * 60_000;
const CUSTOM = "__custom__";

const STATUS_LABEL: Record<SetupStatus, string> = {
  ok: "Done",
  warn: "Needs a look",
  todo: "To do",
  fail: "Failed",
};

type Wait = "waiting" | "timeout";

// ---------- Small pieces ----------

function StatusIcon({ status }: { status: SetupStatus }) {
  const glyph =
    status === "ok" ? (
      <IconCheck size={14} strokeWidth={3} />
    ) : status === "fail" ? (
      <IconX size={13} strokeWidth={3} />
    ) : status === "warn" ? (
      <svg width={14} height={14} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M12 5v9" stroke="currentColor" strokeWidth={3.4} strokeLinecap="round" />
        <circle cx="12" cy="19.5" r="2" fill="currentColor" />
      </svg>
    ) : null;
  return (
    <span className={`setup-status s-${status}`} role="img" aria-label={STATUS_LABEL[status] ?? status}>
      {glyph}
    </span>
  );
}

function isHttp(url: string | null): url is string {
  return !!url && /^https?:\/\//i.test(url);
}

function SetupRow({
  item,
  busy,
  wait,
  onAction,
}: {
  item: SetupItem;
  busy: boolean;
  wait: Wait | undefined;
  onAction: (item: SetupItem, action: SetupAction) => void;
}) {
  const a = item.action;
  const needsYou = item.status === "todo" || item.status === "fail";
  const btnClass = `btn btn-sm ${needsYou ? "btn-primary" : ""}`;
  const aria = a ? `${a.label}, ${item.label}` : undefined;

  let action: ReactNode = null;
  if (a) {
    if (a.kind === "link") {
      action = isHttp(a.url) ? (
        <a className={btnClass} href={a.url} target="_blank" rel="noopener noreferrer" aria-label={`${aria} (opens in a new tab)`}>
          {a.label}
          <IconExternal size={13} />
        </a>
      ) : (
        <button type="button" className={btnClass} disabled title="No link available">
          {a.label}
        </button>
      );
    } else {
      action = (
        <button type="button" className={btnClass} onClick={() => onAction(item, a)} disabled={busy} aria-label={aria}>
          {busy ? <Spinner size={12} /> : a.kind === "terminal" ? <IconTerminal size={14} /> : null}
          {a.label}
        </button>
      );
    }
  }

  return (
    <li className={`setup-row is-${item.status}`} aria-busy={busy || undefined}>
      <StatusIcon status={item.status} />
      <div className="setup-body">
        <div className="setup-label">{item.label}</div>
        {item.detail ? (
          <div className="setup-detail">
            <Ticks text={item.detail} />
          </div>
        ) : null}
        <div aria-live="polite">
          {wait === "waiting" ? (
            <div className="setup-wait">
              <Spinner size={12} />
              <span>Finish signing in in the Terminal window that just opened…</span>
            </div>
          ) : wait === "timeout" ? (
            <div className="setup-wait is-timeout">
              <IconAlert size={14} />
              <span>Still not signed in. When you've finished in Terminal, press Re-check.</span>
            </div>
          ) : null}
        </div>
      </div>
      {action ? <div className="setup-action">{action}</div> : null}
    </li>
  );
}

function RowList(props: {
  items: SetupItem[];
  busy: Record<string, boolean>;
  waits: Record<string, Wait>;
  onAction: (item: SetupItem, action: SetupAction) => void;
}) {
  if (!props.items.length) return null;
  return (
    <ul className="setup-list">
      {props.items.map((it) => (
        <SetupRow key={it.id} item={it} busy={!!props.busy[it.id]} wait={props.waits[it.id]} onAction={props.onAction} />
      ))}
    </ul>
  );
}

function GroupCount({ items }: { items: SetupItem[] }) {
  if (!items.length) return null;
  const ok = items.filter((i) => i.status === "ok").length;
  return (
    <span className="count" aria-label={`${ok} of ${items.length} done`}>
      {ok}/{items.length}
    </span>
  );
}

// ---------- Profile ----------

const EDITABLE = ["name", "crisis_country", "private_vault", "public_vault"] as const;
type Field = (typeof EDITABLE)[number];
type VaultField = "private_vault" | "public_vault";
type Draft = Record<Field, string>;

function toDraft(p: SetupProfile): Draft {
  return {
    name: p.name ?? "",
    crisis_country: p.crisis_country ?? "",
    private_vault: p.private_vault ?? "",
    public_vault: p.public_vault ?? "",
  };
}

function norm(f: Field, v: string): string {
  const t = v.trim();
  return f === "crisis_country" ? t.toUpperCase() : t;
}

const validPath = (v: string) => /^(~|\/)/.test(v.trim());

function VaultPicker({
  id,
  label,
  value,
  custom,
  vaults,
  error,
  describedBy,
  onPick,
  onCustom,
  onBlur,
}: {
  id: string;
  label: string;
  value: string;
  custom: boolean;
  vaults: SetupVault[];
  error: string | undefined;
  describedBy: string;
  onPick: (v: string, custom: boolean) => void;
  onCustom: (v: string) => void;
  onBlur: () => void;
}) {
  const listed = vaults.some((v) => v.path === value);
  const showCustom = custom || (value !== "" && !listed);
  const selectValue = showCustom ? CUSTOM : value;
  const errId = `${id}-err`;
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <select
        id={id}
        value={selectValue}
        aria-describedby={describedBy}
        onChange={(e) => {
          const v = e.target.value;
          if (v === CUSTOM) onPick(value, true);
          else onPick(v, false);
        }}
      >
        {selectValue === "" ? (
          <option value="" disabled>
            Choose a vault…
          </option>
        ) : null}
        {vaults.map((v) => (
          <option key={v.path} value={v.path}>
            {v.name ? `${v.name} — ${v.path}` : v.path}
          </option>
        ))}
        <option value={CUSTOM}>Custom path…</option>
      </select>
      {showCustom ? (
        <input
          id={`${id}-path`}
          type="text"
          className="mono-input"
          value={value}
          placeholder="~/Documents/vault"
          aria-label={`${label}: custom path`}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errId : undefined}
          spellCheck={false}
          autoCapitalize="off"
          autoCorrect="off"
          onChange={(e) => onCustom(e.target.value)}
          onBlur={onBlur}
        />
      ) : null}
      {error ? (
        <span className="field-error" id={errId}>
          {error}
        </span>
      ) : null}
    </div>
  );
}

function ProfileCard({
  profile,
  vaults,
  action,
  children,
  onSaved,
}: {
  profile: SetupProfile;
  vaults: SetupVault[];
  action?: ReactNode;
  children?: ReactNode;
  onSaved: () => Promise<void>;
}) {
  const toast = useToast();
  const incoming = toDraft(profile);
  const incomingKey = JSON.stringify(incoming);
  const [base, setBase] = useState<Draft>(incoming);
  const [draft, setDraft] = useState<Draft>(incoming);
  const [custom, setCustom] = useState<Record<VaultField, boolean>>({ private_vault: false, public_vault: false });
  const [touched, setTouched] = useState<Partial<Record<Field, boolean>>>({});
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Server data refreshes (polls, actions) must not wipe fields the user is editing.
  useEffect(() => {
    const changed = EDITABLE.filter((f) => base[f] !== incoming[f]);
    if (!changed.length) return;
    setDraft((d) => {
      const next = { ...d };
      for (const f of changed) if (d[f] === base[f]) next[f] = incoming[f];
      return next;
    });
    setBase(incoming);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [incomingKey]);

  const errors: Partial<Record<Field, string>> = {};
  const cc = norm("crisis_country", draft.crisis_country);
  if (cc && !/^[A-Z]{2}$/.test(cc)) errors.crisis_country = "Use a 2-letter country code, like LK or GB.";
  for (const f of ["private_vault", "public_vault"] as const) {
    if (norm(f, draft[f]) !== norm(f, base[f]) && !validPath(draft[f])) {
      errors[f] = "Use an absolute path, or one starting with ~.";
    }
  }
  const shown = (f: Field) => (touched[f] ? errors[f] : undefined);

  const body: SetupProfileBody = {};
  for (const f of EDITABLE) {
    const v = norm(f, draft[f]);
    if (v !== norm(f, base[f])) body[f] = v;
  }
  const dirty = Object.keys(body).length > 0;
  const invalid = Object.keys(errors).length > 0;

  const set = (f: Field, v: string) => {
    setDraft((d) => ({ ...d, [f]: v }));
    setErr(null);
  };
  const blur = (f: Field) => setTouched((t) => ({ ...t, [f]: true }));

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setTouched({ name: true, crisis_country: true, private_vault: true, public_vault: true });
    if (!dirty || invalid || saving) return;
    setSaving(true);
    setErr(null);
    try {
      const r = await api.setupProfile(body);
      if (!r.ok) throw new Error("The server didn't save the profile.");
      if (r.profile) {
        const fresh = toDraft(r.profile);
        setBase(fresh);
        setDraft(fresh);
      }
      setCustom({ private_vault: false, public_vault: false });
      setTouched({});
      toast.show("Profile saved.", "ok");
      await onSaved();
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : "Couldn't save the profile.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card title="Profile" id="setup-profile" className="setup-card" action={action}>
      <form className="profile-form" onSubmit={(e) => void save(e)} noValidate>
        <div className="form-grid">
          <div className="field">
            <label className="field-label" htmlFor="pf-name">
              Name
            </label>
            <input
              id="pf-name"
              type="text"
              value={draft.name}
              placeholder="What should your chief of staff call you?"
              autoComplete="given-name"
              onChange={(e) => set("name", e.target.value)}
              onBlur={() => blur("name")}
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="pf-cc">
              Crisis country
            </label>
            <input
              id="pf-cc"
              type="text"
              className="input-cc"
              value={draft.crisis_country}
              maxLength={2}
              placeholder="LK"
              autoComplete="country"
              autoCapitalize="characters"
              spellCheck={false}
              aria-invalid={shown("crisis_country") ? true : undefined}
              aria-describedby={shown("crisis_country") ? "pf-cc-hint pf-cc-err" : "pf-cc-hint"}
              onChange={(e) => set("crisis_country", e.target.value.toUpperCase())}
              onBlur={() => blur("crisis_country")}
            />
            <span className="field-hint" id="pf-cc-hint">
              2-letter country code, used to show the right helpline if you ever need one.
            </span>
            {shown("crisis_country") ? (
              <span className="field-error" id="pf-cc-err">
                {shown("crisis_country")}
              </span>
            ) : null}
          </div>
          {(["private_vault", "public_vault"] as const).map((f) => (
            <VaultPicker
              key={f}
              id={`pf-${f}`}
              label={f === "private_vault" ? "Private vault" : "Public vault"}
              value={draft[f]}
              custom={custom[f]}
              vaults={vaults}
              error={shown(f)}
              describedBy="pf-vault-hint"
              onPick={(v, isCustom) => {
                setCustom((c) => ({ ...c, [f]: isCustom }));
                set(f, v);
                if (!isCustom) blur(f);
              }}
              onCustom={(v) => set(f, v)}
              onBlur={() => blur(f)}
            />
          ))}
        </div>
        <p className="field-hint vault-hint" id="pf-vault-hint">
          <strong>private</strong>: diary, daily notes (never pushed anywhere). <strong>public</strong>: knowledge notes
          (may be a public repo).
        </p>
        <dl className="detected">
          <div>
            <dt>GitHub user</dt>
            <dd>{profile.github_user ? profile.github_user : <span className="muted">not detected</span>}</dd>
          </div>
          <div>
            <dt>Jira site</dt>
            <dd>{profile.jira_site ? profile.jira_site : <span className="muted">not detected</span>}</dd>
          </div>
        </dl>
        <div className="form-foot">
          <button type="submit" className="btn btn-primary" disabled={saving || !dirty}>
            {saving ? <Spinner size={14} /> : null}
            Save
          </button>
          {!dirty && !saving ? <span className="muted small">No unsaved changes</span> : null}
        </div>
        {err ? (
          <p className="inline-error" role="alert">
            <Ticks text={err} />
          </p>
        ) : null}
      </form>
      {children ? <div className="setup-sub">{children}</div> : null}
    </Card>
  );
}

// ---------- Phone ----------

async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall back below */
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

function PhonePairing({ ntfy }: { ntfy: SetupNtfy | null }) {
  const toast = useToast();
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const topic = ntfy?.topic ?? "";
  if (!topic) {
    return (
      <div className="phone-pair">
        <Empty icon={<IconPhone size={20} />}>No notification topic yet. Re-check once the steps above are done.</Empty>
      </div>
    );
  }

  const copy = async () => {
    if (await copyText(topic)) {
      setCopied(true);
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setCopied(false), 2000);
      toast.show("Topic copied.", "ok");
    } else {
      toast.show("Couldn't copy. Select the topic and copy it by hand.", "error");
    }
  };

  return (
    <div className="phone-pair">
      {ntfy?.subscribe_url ? (
        <QrCode text={ntfy.subscribe_url} label={`QR code that subscribes the ntfy app to ${ntfy.subscribe_url}`} />
      ) : null}
      <div className="phone-steps">
        <ol>
          <li>Install the ntfy app.</li>
          <li>
            Scan this, or add topic <code>{topic}</code>.
          </li>
          <li>
            Press <strong>Send test</strong>.
          </li>
        </ol>
        <div className="topic-row">
          <span className="sr-only" id="ntfy-topic-label">
            ntfy topic
          </span>
          <code className="topic" aria-labelledby="ntfy-topic-label">
            {topic}
          </code>
          <button type="button" className="btn btn-sm" onClick={() => void copy()} aria-label="Copy the ntfy topic">
            {copied ? <IconCheck size={14} /> : <IconCopy size={14} />}
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------- Screen ----------

function DonePanel() {
  return (
    <div className="card setup-done" role="status">
      <div className="setup-done-icon" aria-hidden="true">
        <IconCheck size={28} />
      </div>
      <div className="setup-done-body">
        <h2>You're set up.</h2>
        <p className="muted">Your first brief arrives at 07:30 on the next workday.</p>
        <div className="missing-actions">
          <a className="btn btn-primary" href="#/today">
            Go to Today
          </a>
        </div>
      </div>
    </div>
  );
}

export function SetupScreen() {
  const { setup } = useAppData();
  const toast = useToast();
  const [deep, setDeep] = useState(false);
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [waits, setWaits] = useState<Record<string, Wait>>({});
  const [confirming, setConfirming] = useState<{ item: SetupItem; action: SetupAction } | null>(null);
  const watches = useRef(new Map<string, number>());

  const d = setup.data;
  const setSetup = setup.set;
  const refreshSetup = setup.refresh;

  // Fresh numbers when the screen opens (skip if the app just fetched them).
  useEffect(() => {
    const at = setup.updatedAt?.getTime() ?? 0;
    if (Date.now() - at > 10_000) void refreshSetup();
    const w = watches.current;
    return () => {
      w.forEach((t) => window.clearTimeout(t));
      w.clear();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const clearWait = (id: string) =>
    setWaits((w) => {
      const next = { ...w };
      delete next[id];
      return next;
    });

  /** After a `terminal` action: poll every 5 s for up to 2 min, stopping when the item turns ok. */
  const watchTerminal = (item: SetupItem) => {
    const prev = watches.current.get(item.id);
    if (prev !== undefined) window.clearTimeout(prev);
    const deadline = Date.now() + TERMINAL_WAIT_MS;
    setWaits((w) => ({ ...w, [item.id]: "waiting" }));

    const schedule = () => watches.current.set(item.id, window.setTimeout(() => void tick(), TERMINAL_POLL_MS));
    const tick = async () => {
      let done: SetupItem | undefined;
      try {
        const fresh = await api.setup(false);
        if (!watches.current.has(item.id)) return; // unmounted or superseded
        setSetup(fresh);
        done = fresh.items.find((i) => i.id === item.id && i.status === "ok");
      } catch {
        /* a failed poll shouldn't end the wait */
      }
      if (!watches.current.has(item.id)) return;
      if (done) {
        watches.current.delete(item.id);
        clearWait(item.id);
        toast.show(done.detail ? `${done.label}: ${done.detail}` : `${done.label} is ready.`, "ok");
        return;
      }
      if (Date.now() + TERMINAL_POLL_MS > deadline) {
        watches.current.delete(item.id);
        setWaits((w) => ({ ...w, [item.id]: "timeout" }));
        return;
      }
      schedule();
    };
    schedule();
  };

  const perform = async (item: SetupItem, action: SetupAction) => {
    setBusy((b) => ({ ...b, [item.id]: true }));
    if (action.kind === "terminal") clearWait(item.id);
    try {
      const r = await api.setupRun(action.id);
      if (!r.ok) {
        toast.show(r.detail || `${action.label} didn't work.`, "error");
        return;
      }
      if (action.kind === "terminal") watchTerminal(item);
      else toast.show(r.detail || `${item.label}: done.`, "ok");
    } catch (e) {
      toast.show(e instanceof Error ? e.message : `Couldn't run ${action.label}.`, "error");
    } finally {
      setBusy((b) => ({ ...b, [item.id]: false }));
      void refreshSetup();
    }
  };

  const onAction = (item: SetupItem, action: SetupAction) => {
    if (action.kind === "confirm") setConfirming({ item, action });
    else if (action.kind === "run" || action.kind === "terminal") void perform(item, action);
  };

  const recheck = async () => {
    setDeep(true);
    try {
      const fresh = await api.setup(true);
      setSetup(fresh);
      toast.show(fresh.complete ? "Re-checked. Everything's in place." : "Re-checked, connectors included.", "ok");
    } catch (e) {
      toast.show(e instanceof Error ? e.message : "Couldn't re-check.", "error");
    } finally {
      setDeep(false);
    }
  };

  const groups = useMemo(() => {
    const by = new Map<string, SetupItem[]>();
    for (const it of d?.items ?? []) {
      const list = by.get(it.group);
      if (list) list.push(it);
      else by.set(it.group, [it]);
    }
    const known = SETUP_GROUPS as readonly string[];
    const order = [...known, ...[...by.keys()].filter((g) => !known.includes(g))];
    return order.map((name) => ({ name, items: by.get(name) ?? [] }));
  }, [d?.items]);

  const total = d?.items.length ?? 0;
  const ok = d?.items.filter((i) => i.status === "ok").length ?? 0;
  const pct = total ? Math.round((ok / total) * 100) : 0;
  const rowProps = { busy, waits, onAction };

  return (
    <div className="setup">
      <PageHead
        title="Set up me_harness"
        sub={d ? `${ok} of ${total} done.` : "Accounts, vaults, phone and schedule."}
        actions={
          <button
            type="button"
            className="btn"
            onClick={() => void recheck()}
            disabled={deep}
            title="Also checks which claude.ai connectors are loaded. Takes about 3 seconds."
          >
            {deep ? <Spinner size={14} /> : <IconRefresh size={15} />}
            {deep ? "Checking…" : "Re-check (deep)"}
          </button>
        }
      />
      {d ? (
        <div
          className="setup-progress"
          role="progressbar"
          aria-label="Setup progress"
          aria-valuemin={0}
          aria-valuemax={total}
          aria-valuenow={ok}
          aria-valuetext={`${ok} of ${total} done`}
        >
          <span style={{ width: `${pct}%` }} />
        </div>
      ) : null}

      {setup.error ? <ErrorState compact={!!d} error={setup.error} onRetry={() => void refreshSetup()} /> : null}

      {setup.loading ? (
        <Card>
          <Loading rows={5} />
        </Card>
      ) : d ? (
        <>
          {d.complete ? <DonePanel /> : null}
          {groups.map(({ name, items }) => {
            if (name === "Profile") {
              return (
                <ProfileCard
                  key={name}
                  profile={d.profile}
                  vaults={d.vaults}
                  action={<GroupCount items={items} />}
                  onSaved={refreshSetup}
                >
                  <RowList items={items} {...rowProps} />
                </ProfileCard>
              );
            }
            if (name === "Phone") {
              return (
                <Card
                  key={name}
                  title="Phone"
                  id="setup-phone"
                  className="setup-card"
                  action={<GroupCount items={items} />}
                >
                  <RowList items={items} {...rowProps} />
                  <PhonePairing ntfy={d.ntfy} />
                </Card>
              );
            }
            if (!items.length) return null;
            const gid = `setup-${name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
            return (
              <Card key={name} title={name} id={gid} className="setup-card" action={<GroupCount items={items} />}>
                <RowList items={items} {...rowProps} />
              </Card>
            );
          })}
        </>
      ) : null}

      {confirming ? (
        <ConfirmDialog
          title={confirming.item.label}
          body={confirming.action.confirm || "This changes something on your Mac."}
          onCancel={() => setConfirming(null)}
          onConfirm={() => {
            const c = confirming;
            setConfirming(null);
            void perform(c.item, c.action);
          }}
        />
      ) : null}
    </div>
  );
}
