import { useCallback, useEffect, useRef, useState, type ComponentType } from "react";
import { ApiError, api, hasToken } from "./api";
import { CommandBar, type BarMode } from "./components/CommandBar";
import {
  IconAgents,
  IconApprovals,
  IconCheck,
  IconCommand,
  IconMemory,
  IconMoon,
  IconSetup,
  IconSun,
  IconSystem,
  IconToday,
  IconWellbeing,
  IconWork,
} from "./components/Icons";
import { Spinner, Ticks } from "./components/ui";
import { useAppData } from "./data";
import { useHashRoute, useTheme } from "./hooks";
import { AgentsScreen } from "./screens/Agents";
import { ApprovalsScreen } from "./screens/Approvals";
import { MemoryScreen } from "./screens/Memory";
import { SetupScreen } from "./screens/Setup";
import { TodayScreen } from "./screens/Today";
import { WellbeingScreen } from "./screens/Wellbeing";
import { WorkScreen } from "./screens/Work";
import { useToast } from "./toast";

const ROUTES = ["today", "approvals", "work", "agents", "wellbeing", "memory", "setup"] as const;
type Route = (typeof ROUTES)[number];

const NAV: ReadonlyArray<{ id: Route; label: string; Icon: ComponentType<{ size?: number }> }> = [
  { id: "today", label: "Today", Icon: IconToday },
  { id: "approvals", label: "Approvals", Icon: IconApprovals },
  { id: "work", label: "Work", Icon: IconWork },
  { id: "agents", label: "Agents", Icon: IconAgents },
  { id: "wellbeing", label: "Wellbeing", Icon: IconWellbeing },
  { id: "memory", label: "Memory", Icon: IconMemory },
  { id: "setup", label: "Setup", Icon: IconSetup },
];

const SCREENS: Record<Route, ComponentType> = {
  today: TodayScreen,
  approvals: ApprovalsScreen,
  work: WorkScreen,
  agents: AgentsScreen,
  wellbeing: WellbeingScreen,
  memory: MemoryScreen,
  setup: SetupScreen,
};

// First run: open Setup instead of Today at most once per browser session.
const AUTO_SETUP_KEY = "me-setup-auto-opened";
let autoSetupThisLoad = false;
function claimAutoSetup(): boolean {
  if (autoSetupThisLoad) return false;
  autoSetupThisLoad = true;
  try {
    if (window.sessionStorage.getItem(AUTO_SETUP_KEY)) return false;
    window.sessionStorage.setItem(AUTO_SETUP_KEY, "1");
  } catch {
    /* storage unavailable: once per page load instead */
  }
  return true;
}

/** Nav/tab badge: open setup items while incomplete, a check once complete. */
function SetupBadge({ variant }: { variant: "nav" | "tab" }) {
  const { setup, setupOpen } = useAppData();
  const d = setup.data;
  if (!d) return null;
  const cls = variant === "nav" ? "nav-badge" : "tab-badge";
  if (d.complete) {
    return (
      <span className={`${cls} badge-check`} role="img" aria-label="Setup complete">
        <IconCheck size={variant === "nav" ? 13 : 11} strokeWidth={3} />
      </span>
    );
  }
  if (setupOpen <= 0) return null;
  return (
    <span className={cls} aria-label={`${setupOpen} setup ${setupOpen === 1 ? "step" : "steps"} to do`}>
      {setupOpen > 99 ? "99+" : setupOpen}
    </span>
  );
}

const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

function ThemeButton({ pref, onClick }: { pref: "system" | "light" | "dark"; onClick: () => void }) {
  const Icon = pref === "light" ? IconSun : pref === "dark" ? IconMoon : IconSystem;
  const label = `Theme: ${pref}. Switch theme`;
  return (
    <button type="button" className="btn btn-icon" onClick={onClick} aria-label={label} title={label}>
      <Icon size={17} />
    </button>
  );
}

function PausedBanner() {
  const { paused, refreshAll } = useAppData();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  if (!paused) return null;
  const resume = async () => {
    setBusy(true);
    try {
      await api.resume();
      toast.show("Agents resumed.", "ok");
    } catch (e) {
      toast.show(e instanceof Error ? e.message : "Couldn't resume.", "error");
    } finally {
      setBusy(false);
      void refreshAll();
    }
  };
  return (
    <div className="banner banner-warn" role="status">
      <span>Scheduled agents are paused. Nothing runs on its own.</span>
      <button type="button" className="btn btn-sm" onClick={() => void resume()} disabled={busy}>
        {busy ? <Spinner size={12} /> : null} Resume
      </button>
    </div>
  );
}

function ConnectionBanner() {
  const { today, refreshAll } = useAppData();
  const err = today.error;
  if (!hasToken) {
    return (
      <div className="banner banner-bad" role="alert">
        <Ticks text="No access token found. Open this page from `me serve`, or set `VITE_ME_TOKEN` when running the dev server." />
      </div>
    );
  }
  if (!(err instanceof ApiError) || !(err.isNetwork || err.isAuth)) return null;
  return (
    <div className="banner banner-bad" role="alert">
      <span>
        <Ticks
          text={
            err.isNetwork
              ? "Can't reach Mission Control. Is the server running? Start it with `me serve`."
              : "The server rejected this page's token. Reload it from `me serve`."
          }
        />
      </span>
      <button type="button" className="btn btn-sm" onClick={() => void refreshAll()} disabled={today.refreshing}>
        {today.refreshing ? <Spinner size={12} /> : null} Retry
      </button>
    </div>
  );
}

export function App() {
  const [route, go] = useHashRoute(ROUTES, "today");
  const [theme, cycleTheme] = useTheme();
  const { pendingCount, today, setup } = useAppData();
  const online = !today.error && !!today.data;
  const [barOpen, setBarOpen] = useState(false);
  const [barMode, setBarMode] = useState<BarMode>("ask");

  const openBar = useCallback((mode?: BarMode) => {
    if (mode) setBarMode(mode);
    setBarOpen(true);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setBarOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const setupChecked = useRef(false);
  useEffect(() => {
    const d = setup.data;
    if (setupChecked.current || !d) return;
    setupChecked.current = true;
    if (d.complete || (d.profile.name ?? "").trim() || route !== "today") return;
    if (claimAutoSetup()) go("setup");
  }, [setup.data, route, go]);

  useEffect(() => {
    const label = NAV.find((n) => n.id === route)?.label ?? "Today";
    document.title = `${pendingCount ? `(${pendingCount}) ` : ""}${label} · me`;
  }, [route, pendingCount]);

  useEffect(() => {
    document.getElementById("main")?.scrollTo?.(0, 0);
    window.scrollTo(0, 0);
  }, [route]);

  const Screen = SCREENS[route];
  const shortcut = isMac ? "⌘K" : "Ctrl K";

  return (
    <div className="app">
      <a
        href="#main"
        className="skip"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById("main")?.focus();
        }}
      >
        Skip to content
      </a>
      <aside className="sidebar" aria-label="Primary">
        <div className="brand">
          <img src="./icon.svg" alt="" width={28} height={28} />
          <div>
            <div className="brand-name">me</div>
            <div className="brand-sub">Mission Control</div>
          </div>
        </div>
        <button type="button" className="cmd-trigger" onClick={() => openBar("ask")}>
          <IconCommand size={15} />
          <span>Ask or capture</span>
          <kbd>{shortcut}</kbd>
        </button>
        <nav className="side-nav">
          {NAV.map(({ id, label, Icon }) => (
            <a
              key={id}
              href={`#/${id}`}
              className={`nav-item ${route === id ? "is-active" : ""}`}
              aria-current={route === id ? "page" : undefined}
              onClick={(e) => {
                e.preventDefault();
                go(id);
              }}
            >
              <Icon size={18} />
              <span>{label}</span>
              {id === "approvals" && pendingCount > 0 ? (
                <span className="nav-badge" aria-label={`${pendingCount} pending`}>
                  {pendingCount}
                </span>
              ) : null}
              {id === "setup" ? <SetupBadge variant="nav" /> : null}
            </a>
          ))}
        </nav>
        <div className="side-foot">
          <ThemeButton pref={theme} onClick={cycleTheme} />
          <span className="conn small muted" title={today.updatedAt ? `Last updated ${today.updatedAt.toLocaleTimeString()}` : undefined}>
            <span className={`status-dot ${online ? "dot-ok" : today.loading ? "dot-dim" : "dot-bad"}`} aria-hidden="true" />
            {online ? "connected" : today.loading ? "connecting…" : "offline"}
          </span>
        </div>
      </aside>

      <header className="topbar">
        <div className="brand">
          <img src="./icon.svg" alt="" width={24} height={24} />
          <span className="brand-name">me</span>
        </div>
        <div className="topbar-actions">
          <ThemeButton pref={theme} onClick={cycleTheme} />
          <button type="button" className="btn btn-primary btn-sm" onClick={() => openBar("ask")}>
            <IconCommand size={14} /> Ask
          </button>
        </div>
      </header>

      <main id="main" className="main" tabIndex={-1}>
        <ConnectionBanner />
        <PausedBanner />
        <Screen key={route} />
      </main>

      <nav className="tabbar" aria-label="Primary">
        {NAV.map(({ id, label, Icon }) => (
          <a
            key={id}
            href={`#/${id}`}
            className={`tab ${route === id ? "is-active" : ""}`}
            aria-current={route === id ? "page" : undefined}
            onClick={(e) => {
              e.preventDefault();
              go(id);
            }}
          >
            <span className="tab-icon">
              <Icon size={20} />
              {id === "approvals" && pendingCount > 0 ? (
                <span className="tab-badge" aria-label={`${pendingCount} pending`}>
                  {pendingCount > 99 ? "99+" : pendingCount}
                </span>
              ) : null}
              {id === "setup" ? <SetupBadge variant="tab" /> : null}
            </span>
            <span className="tab-label">{label}</span>
          </a>
        ))}
      </nav>

      <CommandBar open={barOpen} mode={barMode} onMode={setBarMode} onClose={() => setBarOpen(false)} />
    </div>
  );
}
