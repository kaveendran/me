import { useEffect, useRef, useState } from "react";
import { api, type CalEvent, type Today } from "../api";
import { LoadBadge } from "../components/LoadBadge";
import { MoodCheckin } from "../components/MoodCheckin";
import { Timeline } from "../components/Timeline";
import { IconAlert, IconCalendar, IconCheck, IconClock, IconFlag, IconSun, IconTarget } from "../components/Icons";
import { StateBanner } from "../components/StateIndicator";
import { Card, Empty, ErrorState, ExtLink, PageHead, RefreshButton, SkeletonCard, Spinner, Ticks } from "../components/ui";
import { useAppData } from "../data";
import { countdown, duration, errText, hmToMin, localDate, nowMin, plural, shortDate } from "../format";
import { useMedia, useNow } from "../hooks";
import { useToast } from "../toast";

function NextMeeting({ events, tomorrow, isToday }: { events: CalEvent[]; tomorrow: CalEvent[]; isToday: boolean }) {
  const now = useNow(1000);
  const nm = nowMin(now);
  const sorted = [...events]
    .map((e) => ({ e, s: hmToMin(e.start) ?? 0, en: hmToMin(e.end) ?? (hmToMin(e.start) ?? 0) + 30 }))
    .sort((a, b) => a.s - b.s);

  if (!isToday) {
    return (
      <Card title="Next meeting" id="next">
        <Empty icon={<IconClock size={16} />}>This brief isn't from today, so there's no live countdown.</Empty>
      </Card>
    );
  }

  const live = sorted.find((x) => x.s <= nm && nm < x.en);
  const next = sorted.find((x) => x.s > nm);
  const firstTomorrow = [...tomorrow].sort((a, b) => (hmToMin(a.start) ?? 0) - (hmToMin(b.start) ?? 0))[0];

  return (
    <Card title="Next meeting" id="next" className="next-card">
      {live ? (
        <div className="next-live">
          <span className="live-dot" aria-hidden="true" />
          <span>
            Now: <strong>{live.e.title}</strong>, ends in {countdown((live.en - nm) * 60)}
          </span>
          {live.e.meet_url ? (
            <ExtLink href={live.e.meet_url} className="btn btn-sm btn-primary">
              Join
            </ExtLink>
          ) : null}
        </div>
      ) : null}
      {next ? (
        <div className="next-main">
          <div className="next-count" aria-live="off">
            <span className="next-count-num">{countdown((next.s - nm) * 60)}</span>
            <span className="muted">until</span>
          </div>
          <div className="next-info">
            <div className="next-title">{next.e.title}</div>
            <div className="muted">
              {next.e.start}–{next.e.end}
              {next.e.attendees > 1 ? ` · ${plural(next.e.attendees, "person", "people")}` : ""}
            </div>
          </div>
          {next.e.meet_url ? (
            <ExtLink href={next.e.meet_url} className="btn btn-sm">
              Join link
            </ExtLink>
          ) : null}
        </div>
      ) : (
        <Empty icon={<IconCheck size={16} />}>
          <span className="empty-lead">{live ? "Nothing after this one today." : "No more meetings today."}</span>
          {firstTomorrow ? (
            <div className="small">
              Tomorrow starts with {firstTomorrow.title} at {firstTomorrow.start}.
            </div>
          ) : null}
        </Empty>
      )}
    </Card>
  );
}

function Top3({ today }: { today: Today }) {
  const plan = today.plan;
  if (plan && plan.top3.length > 0) {
    const done = plan.top3.filter((t) => t.done).length;
    return (
      <Card title="Top 3" id="top3" action={<span className="card-meta">{done}/{plan.top3.length} done</span>}>
        <ol className="top3">
          {plan.top3.map((t, i) => (
            <li key={i} className={t.done ? "is-done" : ""}>
              <span className="tick" aria-hidden="true">
                {t.done ? <IconCheck size={14} /> : null}
              </span>
              <span className="top3-title">{t.title}</span>
              <span className="sr-only">{t.done ? "(done)" : "(not done)"}</span>
            </li>
          ))}
        </ol>
        {plan.also.length ? (
          <div className="also">
            <div className="eyebrow">Also</div>
            <ul className="also-list">
              {plan.also.map((t, i) => (
                <li key={i} className={t.done ? "is-done" : ""}>
                  <span className="tick sm" aria-hidden="true">
                    {t.done ? <IconCheck size={11} /> : null}
                  </span>
                  {t.title}
                  <span className="sr-only">{t.done ? "(done)" : "(not done)"}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </Card>
    );
  }
  const proposed = today.top3_proposed;
  return (
    <Card
      title="Top 3"
      id="top3"
      action={
        proposed.length ? (
          <span className="tag tag-dim">
            <Ticks text="Proposed · run `me plan`" />
          </span>
        ) : undefined
      }
    >
      {proposed.length ? (
        <ol className="top3 proposed">
          {proposed.map((t, i) => (
            <li key={i}>
              <span className="tick num" aria-hidden="true">
                {i + 1}
              </span>
              <div className="top3-body">
                <div className="top3-title">
                  {t.ref ? <ExtLink href={t.ref}>{t.title}</ExtLink> : t.title}
                </div>
                {t.why ? <div className="muted small">{t.why}</div> : null}
                <div className="top3-meta">
                  {t.est_min ? (
                    <span className="tag">
                      <IconClock size={11} />
                      {duration(t.est_min)}
                    </span>
                  ) : null}
                  {t.source ? <span className="tag tag-dim">{t.source}</span> : null}
                </div>
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <Empty icon={<IconTarget size={16} />}>
          No Top 3 yet. <Ticks text="Run `me plan` to pick them." />
        </Empty>
      )}
    </Card>
  );
}

function FocusWindows({ today }: { today: Today }) {
  const fw = today.focus_windows;
  const total = fw.reduce((a, w) => a + (w.minutes || 0), 0);
  const max = fw.reduce((a, w) => Math.max(a, w.minutes || 0), 1);
  return (
    <Card
      title="Focus windows"
      id="focus"
      action={fw.length ? <span className="card-meta">{duration(total)} free</span> : undefined}
    >
      {fw.length ? (
        <ul className="focus-list">
          {fw.map((w, i) => (
            <li key={i}>
              <span className="focus-time">
                {w.start}–{w.end}
              </span>
              <span className="focus-bar" aria-hidden="true">
                <span style={{ width: `${Math.max(6, (w.minutes / max) * 100)}%` }} />
              </span>
              <span className="focus-min">{duration(w.minutes)}</span>
            </li>
          ))}
        </ul>
      ) : (
        <Empty icon={<IconClock size={16} />}>No long gaps today. Short breaks still count.</Empty>
      )}
    </Card>
  );
}

function Risks({ today }: { today: Today }) {
  return (
    <Card title="Risks" id="risks">
      {today.risks.length ? (
        <ul className="risk-list">
          {today.risks.map((r, i) => (
            <li key={i}>
              <IconAlert size={15} />
              <span>{r}</span>
            </li>
          ))}
        </ul>
      ) : (
        <Empty icon={<IconFlag size={16} />}>Nothing flagged. Clear skies.</Empty>
      )}
    </Card>
  );
}

function MissingBrief({ onRan }: { onRan: () => void }) {
  const [state, setState] = useState<"idle" | "starting" | "running">("idle");
  const [err, setErr] = useState<string | null>(null);
  const timers = useRef<number[]>([]);
  const toast = useToast();

  useEffect(() => () => timers.current.forEach((t) => window.clearTimeout(t)), []);

  const run = async () => {
    setState("starting");
    setErr(null);
    try {
      await api.runAgent("brief");
      setState("running");
      toast.show("Brief started. It usually takes about 20 seconds.", "ok");
      [20_000, 40_000, 75_000].forEach((ms) => timers.current.push(window.setTimeout(onRan, ms)));
      timers.current.push(window.setTimeout(() => setState("idle"), 90_000));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Couldn't start the brief.");
      setState("idle");
    }
  };

  return (
    <div className="card missing">
      <div className="missing-icon" aria-hidden="true">
        <IconSun size={20} />
      </div>
      <div className="missing-body">
        <h2>No brief yet today</h2>
        <p className="muted">
          Your morning brief runs at 07:30 on workdays. It pulls your calendar, PRs and Jira together and picks a Top 3.
        </p>
        <div className="missing-actions">
          <button type="button" className="btn btn-primary" onClick={() => void run()} disabled={state !== "idle"}>
            {state === "idle" ? null : <Spinner size={14} />}
            {state === "running" ? "Brief running…" : "Run brief now"}
          </button>
          {state === "running" ? <span className="muted small">This page refreshes when it's ready.</span> : null}
        </div>
        {err ? (
          <p className="inline-error" role="alert">
            <Ticks text={err} />
          </p>
        ) : null}
      </div>
    </div>
  );
}

export function TodayScreen() {
  const { today, refreshAll } = useAppData();
  const openAppearance = () => {
    window.location.hash = "/appearance";
  };
  const now = useNow(60_000);
  const wide = useMedia("(min-width: 1100px)");
  const t = today.data;

  const dateLabel = t?.date ? shortDate(t.date, { weekday: "long", day: "numeric", month: "long" }) : "Today";

  if (today.loading) {
    return (
      <>
        <PageHead title="Today" sub={<span className="skeleton" style={{ width: 120 }} />} />
        <div className="today-grid">
          <div className="col">
            <SkeletonCard rows={3} />
            <SkeletonCard rows={2} />
          </div>
          <div className="col">
            <SkeletonCard rows={1} />
            <SkeletonCard rows={2} />
          </div>
        </div>
      </>
    );
  }

  if (!t) {
    return (
      <>
        <PageHead title="Today" />
        {today.error ? <ErrorState error={today.error} onRetry={() => void today.refresh()} /> : null}
      </>
    );
  }

  const isToday = t.date === localDate(now);
  const blocks = t.plan?.blocks ?? [];

  const header = (
    <PageHead
      title={dateLabel}
      sub={
        t.missing ? (
          "Waiting for this morning's brief."
        ) : (
          <>
            {t.generated_at ? <>Brief from {t.generated_at.slice(11, 16) || t.generated_at}</> : null}
            {!isToday ? <span className="tag tag-warn">not today's brief</span> : null}
          </>
        )
      }
      actions={
        <>
          {!t.missing ? <LoadBadge load={t.load} /> : null}
          <RefreshButton onClick={() => void today.refresh()} busy={today.refreshing} />
        </>
      }
    />
  );

  const errors = t.errors ?? [];
  const errorsBox = errors.length ? (
    <details className="soft-note">
      <summary>
        {plural(errors.length, "source")} didn't load in this brief
      </summary>
      <ul>
        {errors.map((e, i) => (
          <li key={i}>{errText(e)}</li>
        ))}
      </ul>
    </details>
  ) : null;

  const checkin = (
    <Card title="Check in" id="checkin">
      <MoodCheckin compact />
    </Card>
  );

  if (t.missing) {
    return (
      <>
        {header}
        <StateBanner onAppearance={openAppearance} />
        {today.error ? <ErrorState compact error={today.error} onRetry={() => void today.refresh()} /> : null}
        <MissingBrief onRan={() => void refreshAll()} />
        <div className="grid-2">
          {t.plan ? <Top3 today={t} /> : null}
          {checkin}
        </div>
      </>
    );
  }

  const headline = t.headline ? <p className="headline">{t.headline}</p> : null;

  const timeline = (
    <Card
      title="Schedule"
      id="schedule"
      action={<span className="card-meta">{duration(t.meeting_min)} in meetings</span>}
    >
      {t.events.length || blocks.length ? (
        <Timeline events={t.events} blocks={blocks} now={now} isToday={isToday} />
      ) : (
        <Empty icon={<IconCalendar size={16} />}>No meetings today. A wide-open day.</Empty>
      )}
    </Card>
  );

  const next = <NextMeeting events={t.events} tomorrow={t.tomorrow ?? []} isToday={isToday} />;

  return (
    <>
      {header}
      {headline}
      <StateBanner onAppearance={openAppearance} />
      {today.error ? <ErrorState compact error={today.error} onRetry={() => void today.refresh()} /> : null}
      {errorsBox}
      {wide ? (
        <div className="today-grid">
          <div className="col">
            <Top3 today={t} />
            {timeline}
          </div>
          <div className="col">
            {next}
            {checkin}
            <FocusWindows today={t} />
            <Risks today={t} />
          </div>
        </div>
      ) : (
        <div className="stack">
          {next}
          <Top3 today={t} />
          {checkin}
          {timeline}
          <FocusWindows today={t} />
          <Risks today={t} />
        </div>
      )}
    </>
  );
}
