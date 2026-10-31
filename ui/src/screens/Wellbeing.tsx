import { api, type Load, type WellbeingDay } from "../api";
import { BAND_LABEL, bandOf } from "../components/LoadBadge";
import { MoodCheckin } from "../components/MoodCheckin";
import { Sparkline, type Point } from "../components/Sparkline";
import { IconSpark } from "../components/Icons";
import { Card, Empty, ErrorState, Loading, PageHead, RefreshButton } from "../components/ui";
import { clockTime, shortDate } from "../format";
import { useResource } from "../hooks";

const BAND_NOTE = {
  ok: "A steady day. Nice.",
  elevated: "A fuller day than usual. Leave a little room between things.",
  high: "A heavy day. Be kind to yourself and protect a break or two.",
} as const;

function LoadToday({ load }: { load: Load | null }) {
  if (!load) {
    return (
      <Card title="Today's load" id="load">
        <Empty>No load score yet today. It appears after the morning brief.</Empty>
      </Card>
    );
  }
  const band = bandOf(load);
  const score = Math.max(0, Math.min(100, Math.round(load.score)));
  return (
    <Card title="Today's load" id="load">
      <div className={`load-today band-${band}`}>
        <div className="load-today-num">
          <span className="big">{score}</span>
          <span className="muted">/ 100</span>
        </div>
        <div className="load-today-text">
          <div className="load-today-band">
            <span className="band-dot" aria-hidden="true" /> {BAND_LABEL[band]}
          </div>
          <div className="muted small">{BAND_NOTE[band]}</div>
        </div>
      </div>
      <div
        className={`meter band-${band}`}
        role="meter"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={score}
        aria-label="Load score"
      >
        <span style={{ width: `${score}%` }} />
      </div>
      {load.reasons?.length ? (
        <ul className="reasons">
          {load.reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      ) : null}
    </Card>
  );
}

const series = (days: WellbeingDay[], f: (d: WellbeingDay) => number | null): Point[] =>
  days.map((d) => ({ date: d.date, value: f(d) }));

const fmt1 = (v: number) => (Math.round(v * 10) / 10).toString();
const fmtInt = (v: number) => Math.round(v).toString();
const fmtHours = (v: number) => `${Math.round(v * 10) / 10}h`;

export function WellbeingScreen() {
  const res = useResource((s) => api.wellbeing(s), null);
  const w = res.data;
  const days = w?.days ?? [];
  const meetMaxH = Math.max(4, ...days.map((d) => (d.meeting_min ?? 0) / 60));
  const meetHi = Math.ceil(meetMaxH / 2) * 2;

  return (
    <>
      <PageHead
        title="Wellbeing"
        sub="A gentle look at the last two weeks. Private to you."
        actions={<RefreshButton onClick={() => void res.refresh()} busy={res.refreshing} />}
      />
      {res.error ? <ErrorState compact={!!w} error={res.error} onRetry={() => void res.refresh()} /> : null}
      {res.loading ? (
        <div className="grid-2">
          <Card>
            <Loading rows={3} />
          </Card>
          <Card>
            <Loading rows={3} />
          </Card>
        </div>
      ) : w ? (
        <div className="stack">
          <div className="grid-2">
            <LoadToday load={w.today} />
            <Card title="Check in" id="wb-checkin">
              <MoodCheckin onLogged={() => void res.refresh()} />
              {w.checkins_today.length ? (
                <div className="checkins">
                  <div className="eyebrow">Today</div>
                  <ul>
                    {w.checkins_today.map((c, i) => (
                      <li key={`${c.ts}-${i}`}>
                        <span className="mono muted">{clockTime(c.ts)}</span>
                        {c.mood != null ? <span>mood {c.mood}</span> : null}
                        {c.energy != null ? <span>energy {c.energy}</span> : null}
                        {c.stress != null ? <span>stress {c.stress}</span> : null}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : (
                <p className="muted small checkins-empty">No check-ins yet today.</p>
              )}
            </Card>
          </div>

          <Card title="Last 14 days" id="trends">
            {days.length ? (
              <div className="multiples">
                <Sparkline
                  title="Mood"
                  yLabel="mood, 1 to 5"
                  points={series(days, (d) => d.mood)}
                  domain={[1, 5]}
                  format={fmt1}
                />
                <Sparkline
                  title="Energy"
                  yLabel="energy, 1 to 5"
                  points={series(days, (d) => d.energy)}
                  domain={[1, 5]}
                  format={fmt1}
                />
                <Sparkline
                  title="Load"
                  yLabel="load score, 0 to 100"
                  points={series(days, (d) => d.load)}
                  domain={[0, 100]}
                  format={fmtInt}
                />
                <Sparkline
                  title="Meeting hours"
                  yLabel="hours in meetings"
                  points={series(days, (d) => (d.meeting_min == null ? null : d.meeting_min / 60))}
                  domain={[0, meetHi]}
                  format={fmtHours}
                />
              </div>
            ) : (
              <Empty>Trends appear after a few days of check-ins.</Empty>
            )}
          </Card>

          <div className="grid-2">
            <Card title="Wins jar" id="wins" action={w.wins.length ? <span className="count">{w.wins.length}</span> : undefined}>
              {w.wins.length ? (
                <ul className="wins">
                  {w.wins.map((win, i) => (
                    <li key={`${win.date}-${i}`}>
                      <span className="win-date muted">{shortDate(win.date, { day: "numeric", month: "short" })}</span>
                      <span>{win.text}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <Empty>The jar is empty for now. Small wins count too.</Empty>
              )}
            </Card>
            <Card title="This week's experiment" id="exp">
              {w.experiment ? (
                <div className="experiment">
                  <IconSpark size={18} />
                  <p>{w.experiment}</p>
                </div>
              ) : (
                <Empty>No experiment this week. Your weekly review can suggest one.</Empty>
              )}
            </Card>
          </div>
        </div>
      ) : null}
    </>
  );
}
