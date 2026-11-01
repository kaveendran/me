import type { ReactNode } from "react";
import { api, type CiStatus, type JiraIssue } from "../api";
import { IconApprovals, IconCheck, IconWork } from "../components/Icons";
import { Card, Empty, ErrorState, ExtLink, PageHead, RefreshButton, SkeletonCard } from "../components/ui";
import { absTime, relTime } from "../format";
import { useResource } from "../hooks";

const CI_LABEL: Record<CiStatus, string> = {
  passing: "passing",
  failing: "failing",
  pending: "running",
  none: "no CI",
  unknown: "unknown",
};
const CI_TONE: Record<CiStatus, string> = {
  passing: "tone-ok",
  failing: "tone-bad",
  pending: "tone-warn",
  none: "tone-dim",
  unknown: "tone-dim",
};

function CiPill({ ci }: { ci: CiStatus | undefined }) {
  const c: CiStatus = ci && ci in CI_LABEL ? ci : "unknown";
  if (!ci) return null;
  return (
    <span className={`status-pill ${CI_TONE[c]}`} title={`CI ${CI_LABEL[c]}`}>
      <span className="pill-dot" aria-hidden="true" />
      CI {CI_LABEL[c]}
    </span>
  );
}

/** Fresh items first; stale ones collapsed under a "stale (n)" disclosure. */
function StaleSplit<T extends { stale: boolean }>({
  items,
  render,
  empty,
}: {
  items: T[];
  render: (t: T) => ReactNode;
  empty: string;
}) {
  const fresh = items.filter((i) => !i.stale);
  const stale = items.filter((i) => i.stale);
  if (!items.length) return <Empty icon={<IconCheck size={16} />}>{empty}</Empty>;
  return (
    <>
      {fresh.length ? <ul className="work-list">{fresh.map(render)}</ul> : <p className="muted small">Only stale items here.</p>}
      {stale.length ? (
        <details className="stale">
          <summary>Stale ({stale.length})</summary>
          <ul className="work-list">{stale.map(render)}</ul>
        </details>
      ) : null}
    </>
  );
}

const CAT_ORDER = ["To Do", "In Progress", "Done"];

function groupJira(items: JiraIssue[]): Array<[string, JiraIssue[]]> {
  const m = new Map<string, JiraIssue[]>();
  for (const j of items) {
    const k = j.status_category || "Other";
    const arr = m.get(k);
    if (arr) arr.push(j);
    else m.set(k, [j]);
  }
  const rank = (k: string) => {
    const i = CAT_ORDER.findIndex((c) => c.toLowerCase() === k.toLowerCase());
    return i < 0 ? 1.5 : i; // unknown categories sit between In Progress and Done
  };
  return [...m.entries()].sort((a, b) => rank(a[0]) - rank(b[0]) || a[0].localeCompare(b[0]));
}

export function WorkScreen() {
  const res = useResource((s) => api.work(s), null);
  const w = res.data;

  return (
    <>
      <PageHead
        title="Work"
        sub={res.updatedAt ? `Updated ${relTime(res.updatedAt.toISOString())}` : "Reviews, PRs, Jira and CI"}
        actions={<RefreshButton onClick={() => void res.refresh()} busy={res.refreshing} />}
      />
      {res.error ? <ErrorState compact={!!w} error={res.error} onRetry={() => void res.refresh()} /> : null}
      {res.loading ? (
        <div className="grid-2">
          {[0, 1, 2, 3].map((i) => (
            <SkeletonCard key={i} rows={3} />
          ))}
        </div>
      ) : w ? (
        <div className="grid-2">
          <Card title="Review requests" id="reviews" action={<span className="count">{w.reviews.length}</span>}>
            <StaleSplit
              items={w.reviews}
              empty="No one is waiting on your review."
              render={(r) => (
                <li key={`${r.repo}#${r.number}`} className={r.stale ? "is-stale" : ""}>
                  <ExtLink href={r.url} className="work-title">
                    {r.title}
                  </ExtLink>
                  <div className="work-meta">
                    <span className="mono">
                      {r.repo}#{r.number}
                    </span>
                    {r.author ? <span>by {r.author}</span> : null}
                  </div>
                </li>
              )}
            />
          </Card>

          <Card title="Your PRs" id="prs" action={<span className="count">{w.my_prs.length}</span>}>
            <StaleSplit
              items={w.my_prs}
              empty="No open pull requests."
              render={(p) => (
                <li key={`${p.repo}#${p.number}`} className={p.stale ? "is-stale" : ""}>
                  <ExtLink href={p.url} className="work-title">
                    {p.title}
                  </ExtLink>
                  <div className="work-meta">
                    <span className="mono">
                      {p.repo}#{p.number}
                    </span>
                    {p.draft ? <span className="tag tag-dim">draft</span> : null}
                    <CiPill ci={p.ci} />
                  </div>
                </li>
              )}
            />
          </Card>

          <Card title="Jira" id="jira" action={<span className="count">{w.jira.length}</span>}>
            {w.jira.length ? (
              groupJira(w.jira).map(([cat, items]) => {
                const list = (
                  <ul className="work-list">
                    {items.map((j) => (
                      <li key={j.key}>
                        <ExtLink href={j.url} className="work-title">
                          {j.summary}
                        </ExtLink>
                        <div className="work-meta">
                          <span className="mono">{j.key}</span>
                          <span>{j.status}</span>
                          {j.priority ? (
                            <span className={`tag ${/high|highest|critical|blocker/i.test(j.priority) ? "tag-warn" : "tag-dim"}`}>
                              {j.priority}
                            </span>
                          ) : null}
                        </div>
                      </li>
                    ))}
                  </ul>
                );
                const isDone = cat.toLowerCase() === "done";
                return isDone ? (
                  <details key={cat} className="jira-group stale">
                    <summary>
                      {cat} ({items.length})
                    </summary>
                    {list}
                  </details>
                ) : (
                  <div key={cat} className="jira-group">
                    <div className="eyebrow">
                      {cat} <span className="muted">({items.length})</span>
                    </div>
                    {list}
                  </div>
                );
              })
            ) : (
              <Empty icon={<IconCheck size={16} />}>No Jira issues assigned.</Empty>
            )}
          </Card>

          <Card title="CI diagnoses" id="diag" action={<span className="count">{w.diagnoses.length}</span>}>
            {w.diagnoses.length ? (
              <ul className="diag-list">
                {w.diagnoses.map((d, i) => (
                  <li key={`${d.pr_url ?? ""}-${i}`}>
                    <div className="diag-head">
                      <ExtLink href={d.pr_url ?? ""} className="work-title">
                        {(d.pr_url ?? "Pull request").replace(/^https?:\/\/(www\.)?github\.com\//, "")}
                      </ExtLink>
                      <span className="card-meta" title={absTime(d.at)}>
                        {relTime(d.at)}
                      </span>
                    </div>
                    {d.summary ? <p>{d.summary}</p> : null}
                    <dl className="proposal-meta">
                      {d.likely_cause ? (
                        <>
                          <dt>Likely cause</dt>
                          <dd>{d.likely_cause}</dd>
                        </>
                      ) : null}
                      {d.suggested_fix ? (
                        <>
                          <dt>Suggested fix</dt>
                          <dd>{d.suggested_fix}</dd>
                        </>
                      ) : null}
                    </dl>
                  </li>
                ))}
              </ul>
            ) : (
              <Empty icon={<IconApprovals size={16} />}>No CI failures diagnosed.</Empty>
            )}
          </Card>

          {w.issues?.length ? (
            <Card title="Issues" id="issues" action={<span className="count">{w.issues.length}</span>}>
              <StaleSplit
                items={w.issues}
                empty="No issues."
                render={(it) => (
                  <li key={`${it.repo}#${it.number}`} className={it.stale ? "is-stale" : ""}>
                    <ExtLink href={it.url} className="work-title">
                      {it.title}
                    </ExtLink>
                    <div className="work-meta">
                      <span className="mono">
                        {it.repo}#{it.number}
                      </span>
                    </div>
                  </li>
                )}
              />
            </Card>
          ) : null}
        </div>
      ) : !res.error ? (
        <Card>
          <Empty icon={<IconWork size={16} />}>Nothing to show yet.</Empty>
        </Card>
      ) : null}
    </>
  );
}
