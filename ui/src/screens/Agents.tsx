import { Fragment, useEffect, useRef, useState } from "react";
import { api, type Agent } from "../api";
import { IconAgents, IconPause, IconPlay } from "../components/Icons";
import { Card, Empty, ErrorState, PageHead, RefreshButton, SkeletonCard, Spinner } from "../components/ui";
import { useAppData } from "../data";
import { absTime, money, relTime } from "../format";
import { useNow } from "../hooks";
import { useToast } from "../toast";

const STATUS_LABEL: Record<string, string> = { ok: "OK", error: "Error", never: "Never run" };

function dotClass(status: string): string {
  if (status === "ok") return "dot-ok";
  if (status === "never") return "dot-dim";
  if (status === "error" || status === "failed" || status === "timeout") return "dot-bad";
  return "dot-warn"; // e.g. a run still in progress
}

function KillSwitch({ paused }: { paused: boolean }) {
  const { refreshAll } = useAppData();
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  const toggle = async () => {
    setBusy(true);
    try {
      const r = paused ? await api.resume() : await api.pause();
      toast.show(r.paused ? "All scheduled agents paused." : "Agents resumed.", r.paused ? "info" : "ok");
    } catch (e) {
      toast.show(e instanceof Error ? e.message : "Couldn't change the kill switch.", "error");
    } finally {
      setBusy(false);
      void refreshAll();
    }
  };

  return (
    <div className={`kill ${paused ? "is-paused" : ""}`}>
      <div className="kill-text">
        <div className="kill-title">
          <span className={`status-dot ${paused ? "dot-warn" : "dot-ok"}`} aria-hidden="true" />
          {paused ? "All scheduled agents are paused" : "Agents are running on schedule"}
        </div>
        <div className="muted small">
          {paused
            ? "Nothing runs on its own until you resume. Manual runs still work."
            : "Kill switch: pause stops every scheduled job immediately."}
        </div>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={!paused}
        aria-label="Scheduled agents"
        className={`btn ${paused ? "btn-primary" : "btn-danger"} kill-btn`}
        onClick={() => void toggle()}
        disabled={busy}
      >
        {busy ? <Spinner size={13} /> : paused ? <IconPlay size={14} /> : <IconPause size={14} />}
        {paused ? "Resume all" : "Pause all"}
      </button>
    </div>
  );
}

function AgentRow({ a, now, onRun, running }: { a: Agent; now: Date; onRun: () => void; running: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <Fragment>
      <tr className={dotClass(a.status) === "dot-bad" ? "is-error" : ""}>
        <td data-label="Agent">
          <div className="agent-name">{a.label || a.name}</div>
          <div className="muted mono small">{a.name}</div>
        </td>
        <td data-label="Schedule">{a.schedule || <span className="muted">manual</span>}</td>
        <td data-label="Last run">
          <span title={absTime(a.last_run)}>{a.last_run ? relTime(a.last_run, now) : <span className="muted">never</span>}</span>
        </td>
        <td data-label="Status">
          <span className="status-cell">
            <span
              className={`status-dot ${dotClass(a.status)}`}
              aria-hidden="true"
            />
            {STATUS_LABEL[a.status] ?? a.status}
            {a.last_error ? (
              <button
                type="button"
                className="link-btn"
                aria-expanded={open}
                onClick={() => setOpen((o) => !o)}
              >
                {open ? "hide error" : "show error"}
              </button>
            ) : null}
          </span>
        </td>
        <td data-label="Runs 7d" className="num">
          {a.runs_7d}
        </td>
        <td data-label="Cost 7d" className="num">
          {money(a.cost_7d)}
        </td>
        <td className="actions-cell">
          <button type="button" className="btn btn-sm" onClick={onRun} disabled={running}>
            {running ? <Spinner size={12} /> : <IconPlay size={13} />}
            Run now
          </button>
        </td>
      </tr>
      {open && a.last_error ? (
        <tr className="error-row">
          <td colSpan={7}>
            <pre className="error-pre">{a.last_error}</pre>
          </td>
        </tr>
      ) : null}
    </Fragment>
  );
}

export function AgentsScreen() {
  const { agents, refreshAll } = useAppData();
  const toast = useToast();
  const now = useNow(30_000);
  const [running, setRunning] = useState<Record<string, boolean>>({});
  const timers = useRef<number[]>([]);
  useEffect(() => () => timers.current.forEach((t) => window.clearTimeout(t)), []);

  const d = agents.data;

  const run = async (a: Agent) => {
    setRunning((r) => ({ ...r, [a.name]: true }));
    try {
      await api.runAgent(a.name);
      toast.show(`Started ${a.label || a.name}. It runs in the background.`, "ok");
      [4000, 20_000].forEach((ms) => timers.current.push(window.setTimeout(() => void refreshAll(), ms)));
    } catch (e) {
      toast.show(e instanceof Error ? e.message : `Couldn't start ${a.name}.`, "error");
    } finally {
      timers.current.push(window.setTimeout(() => setRunning((r) => ({ ...r, [a.name]: false })), 3000));
      void refreshAll();
    }
  };

  return (
    <>
      <PageHead
        title="Agents"
        sub="What runs on its own, when it last ran, and what it cost."
        actions={<RefreshButton onClick={() => void agents.refresh()} busy={agents.refreshing} />}
      />
      {agents.error ? <ErrorState compact={!!d} error={agents.error} onRetry={() => void agents.refresh()} /> : null}
      {agents.loading ? (
        <>
          <div className="agents-top">
            <SkeletonCard rows={1} />
            <SkeletonCard rows={1} />
            <SkeletonCard rows={1} />
          </div>
          <SkeletonCard rows={5} />
        </>
      ) : d ? (
        <>
          <div className="agents-top">
            <KillSwitch paused={d.paused} />
            <div className="stat">
              <div className="stat-label">Spend today</div>
              <div className="stat-value">{money(d.spend_today)}</div>
            </div>
            <div className="stat">
              <div className="stat-label">Spend, last 7 days</div>
              <div className="stat-value">{money(d.spend_7d)}</div>
            </div>
          </div>
          <Card className="table-card">
            {d.agents.length ? (
              <table className="agents-table">
                <thead>
                  <tr>
                    <th scope="col">Agent</th>
                    <th scope="col">Schedule</th>
                    <th scope="col">Last run</th>
                    <th scope="col">Status</th>
                    <th scope="col" className="num">
                      Runs 7d
                    </th>
                    <th scope="col" className="num">
                      Cost 7d
                    </th>
                    <th scope="col">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {d.agents.map((a) => (
                    <AgentRow key={a.name} a={a} now={now} running={!!running[a.name]} onRun={() => void run(a)} />
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="card-pad">
                <Empty icon={<IconAgents size={16} />}>No agents registered yet.</Empty>
              </div>
            )}
          </Card>
        </>
      ) : null}
    </>
  );
}
