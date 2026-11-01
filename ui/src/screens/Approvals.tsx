import { useState, type ReactElement } from "react";
import { api, type Proposal, type ProposalsResponse } from "../api";
import { IconApprovals, IconCheck, IconShield, IconX } from "../components/Icons";
import { Card, Empty, ErrorState, ExtLink, PageHead, RefreshButton, SkeletonCard, Spinner, Ticks } from "../components/ui";
import { useAppData } from "../data";
import { absTime, plural, relTime } from "../format";
import { useResource, type Resource } from "../hooks";
import { useToast } from "../toast";

type Filter = "pending" | "all";
type Busy = "approve" | "reject";
interface Outcome {
  status: string;
  result: string;
}

const STATUS_TONE: Record<string, string> = {
  pending: "tone-info",
  approved: "tone-info",
  executed: "tone-ok",
  rejected: "tone-dim",
  failed: "tone-bad",
};

function ProposalCard({
  p,
  busy,
  outcome,
  locked,
  onApprove,
  onReject,
}: {
  p: Proposal;
  busy: Busy | undefined;
  outcome: Outcome | undefined;
  locked: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  const isPending = p.status === "pending" && !outcome;
  const shownStatus = outcome?.status ?? p.status;
  const result = outcome?.result ?? p.result;
  return (
    <article className={`proposal ${busy ? "is-busy" : ""}`} aria-busy={busy ? true : undefined}>
      <header className="proposal-head">
        <span className="tag tag-agent">{p.agent}</span>
        <code className="proposal-action">{p.action}</code>
        {p.safe ? (
          <span className="tag tag-safe" title="Reversible and private: can be batch-approved">
            <IconShield size={11} /> safe
          </span>
        ) : null}
        {shownStatus !== "pending" ? (
          <span className={`status-pill ${STATUS_TONE[shownStatus] ?? "tone-dim"}`}>{shownStatus}</span>
        ) : null}
        <span className="proposal-time muted" title={absTime(p.created)}>
          {relTime(p.created)}
        </span>
      </header>
      {p.target ? (
        <div className="proposal-target">
          {/^https?:\/\//.test(p.target) ? <ExtLink href={p.target}>{p.target}</ExtLink> : <code>{p.target}</code>}
        </div>
      ) : null}
      <pre className="proposal-preview">{p.preview}</pre>
      <dl className="proposal-meta">
        {p.reason ? (
          <>
            <dt>Why</dt>
            <dd>{p.reason}</dd>
          </>
        ) : null}
        {p.undo ? (
          <>
            <dt>Undo</dt>
            <dd>{p.undo}</dd>
          </>
        ) : null}
        {result ? (
          <>
            <dt>Result</dt>
            <dd className={shownStatus === "failed" ? "text-bad" : ""}>{result}</dd>
          </>
        ) : null}
      </dl>
      {isPending ? (
        <div className="proposal-actions">
          <button type="button" className="btn btn-primary" onClick={onApprove} disabled={!!busy || locked}>
            {busy === "approve" ? <Spinner size={13} /> : <IconCheck size={14} />}
            {busy === "approve" ? "Approving…" : "Approve"}
          </button>
          <button type="button" className="btn btn-ghost" onClick={onReject} disabled={!!busy || locked}>
            {busy === "reject" ? <Spinner size={13} /> : <IconX size={14} />}
            Reject
          </button>
          {busy === "approve" ? (
            <span className="muted small" role="status">
              Running the action. This can take up to a minute.
            </span>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

function AllProposals({ children }: { children: (r: Resource<ProposalsResponse>) => ReactElement }) {
  const r = useResource((s) => api.proposals("all", s), 60_000);
  return children(r);
}

function ProposalsView({ res, filter }: { res: Resource<ProposalsResponse>; filter: Filter }) {
  const { refreshAll } = useAppData();
  const toast = useToast();
  const [busy, setBusy] = useState<Record<string, Busy>>({});
  const [outcomes, setOutcomes] = useState<Record<string, Outcome>>({});
  const [batch, setBatch] = useState(false);

  const list = (res.data?.proposals ?? [])
    .filter((p) => filter === "all" || p.status === "pending")
    .sort((a, b) => {
      if ((a.status === "pending") !== (b.status === "pending")) return a.status === "pending" ? -1 : 1;
      return (b.created || "").localeCompare(a.created || "");
    });
  const safePending = list.filter((p) => p.safe && p.status === "pending" && !outcomes[p.id]);

  const after = async () => {
    await Promise.all([refreshAll(), res.refresh()]);
  };

  const setB = (id: string, b: Busy | null) =>
    setBusy((m) => {
      const n = { ...m };
      if (b) n[id] = b;
      else delete n[id];
      return n;
    });

  const approve = async (p: Proposal) => {
    setB(p.id, "approve");
    try {
      const r = await api.approve(p.id);
      setOutcomes((o) => ({ ...o, [p.id]: { status: r.status, result: r.result } }));
      toast.show(r.status === "executed" ? `Done: ${p.action}` : `Approved, but it failed: ${r.result}`, r.status === "executed" ? "ok" : "error");
    } catch (e) {
      toast.show(e instanceof Error ? e.message : "Approve failed.", "error");
    } finally {
      setB(p.id, null);
      void after();
    }
  };

  const reject = async (p: Proposal) => {
    setB(p.id, "reject");
    try {
      const r = await api.reject(p.id);
      if (r.ok) {
        setOutcomes((o) => ({ ...o, [p.id]: { status: "rejected", result: "" } }));
        toast.show(`Rejected: ${p.action}`);
      } else {
        toast.show("Couldn't reject it. It may already have been decided.", "error");
      }
    } catch (e) {
      toast.show(e instanceof Error ? e.message : "Reject failed.", "error");
    } finally {
      setB(p.id, null);
      void after();
    }
  };

  const approveSafe = async () => {
    setBatch(true);
    safePending.forEach((p) => setB(p.id, "approve"));
    try {
      const r = await api.approveSafe();
      const results = r.results ?? {};
      setOutcomes((o) => {
        const n = { ...o };
        for (const id of r.approved ?? []) n[id] = { status: results[id] ?? "approved", result: "" };
        return n;
      });
      const vals = Object.values(results);
      const failed = vals.filter((v) => v === "failed").length;
      const count = (r.approved ?? []).length;
      toast.show(
        count === 0
          ? "No safe proposals to approve."
          : `Approved ${plural(count, "safe action")}${failed ? `, ${failed} failed` : ""}.`,
        failed ? "error" : "ok",
      );
    } catch (e) {
      toast.show(e instanceof Error ? e.message : "Batch approve failed.", "error");
    } finally {
      setBusy({});
      setBatch(false);
      void after();
    }
  };

  return (
    <>
      <div className="toolbar">
        <button
          type="button"
          className="btn btn-safe"
          onClick={() => void approveSafe()}
          disabled={batch || safePending.length === 0}
        >
          {batch ? <Spinner size={13} /> : <IconShield size={14} />}
          {batch ? "Approving safe…" : `Approve all safe (${safePending.length})`}
        </button>
        <RefreshButton onClick={() => void res.refresh()} busy={res.refreshing} />
      </div>
      {res.error ? <ErrorState compact error={res.error} onRetry={() => void res.refresh()} /> : null}
      {res.loading ? (
        <div className="proposal-list">
          <SkeletonCard rows={2} />
          <SkeletonCard rows={2} />
        </div>
      ) : list.length === 0 && !res.error ? (
        <Card>
          <Empty icon={<IconApprovals size={16} />}>
            {filter === "pending" ? "Nothing waiting for you. Agents will queue anything that needs a yes." : "No proposals yet."}
          </Empty>
        </Card>
      ) : (
        <div className="proposal-list">
          {list.map((p) => (
            <ProposalCard
              key={p.id}
              p={p}
              busy={busy[p.id]}
              outcome={outcomes[p.id]}
              locked={batch}
              onApprove={() => void approve(p)}
              onReject={() => void reject(p)}
            />
          ))}
        </div>
      )}
    </>
  );
}

export function ApprovalsScreen() {
  const { pending, pendingCount } = useAppData();
  const [filter, setFilter] = useState<Filter>("pending");

  return (
    <>
      <PageHead
        title="Approvals"
        sub={
          <Ticks
            text={
              pendingCount
                ? `${plural(pendingCount, "proposal")} waiting. Nothing outward-facing happens without your yes.`
                : "Agents queue outward-facing actions here for your yes."
            }
          />
        }
        actions={
          <div className="seg" role="radiogroup" aria-label="Show">
            {(["pending", "all"] as const).map((f) => (
              <button
                key={f}
                type="button"
                role="radio"
                aria-checked={filter === f}
                className={filter === f ? "is-on" : ""}
                onClick={() => setFilter(f)}
              >
                {f === "pending" ? "Pending" : "All"}
              </button>
            ))}
          </div>
        }
      />
      {filter === "pending" ? (
        <ProposalsView res={pending} filter="pending" />
      ) : (
        <AllProposals>{(r) => <ProposalsView res={r} filter="all" />}</AllProposals>
      )}
    </>
  );
}
