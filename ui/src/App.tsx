import { useCallback, useEffect, useMemo, useRef, useState, type ComponentType } from "react";
import { ApiError, api, hasToken, type ThemeMode } from "./api";
import { CommandBar, type BarMode, type Command } from "./components/CommandBar";
import {
  IconAgents,
  IconApprovals,
  IconCheck,
  IconCommand,
  IconMemory,
  IconMoon,
  IconPalette,
  IconSetup,
  IconSun,
  IconSystem,
  IconToday,
  IconWellbeing,
  IconWork,
} from "./components/Icons";
import { StatePill } from "./components/StateIndicator";
import { Spinner, Ticks } from "./components/ui";
import { useAppData } from "./data";
import { useHashRoute } from "./hooks";
import { AgentsScreen } from "./screens/Agents";
import { AppearanceScreen } from "./screens/Appearance";
import { ApprovalsScreen } from "./screens/Approvals";
import { MemoryScreen } from "./screens/Memory";
import { SetupScreen } from "./screens/Setup";
import { TodayScreen } from "./screens/Today";
import { WellbeingScreen } from "./screens/Wellbeing";
import { WorkScreen } from "./screens/Work";
import { useTheme } from "./theme";
import { useToast } from "./toast";

const ROUTES = ["today", "approvals", "work", "agents", "wellbeing", "memory", "setup", "appearance"] as const;
type Route = (typeof ROUTES)[number];

type NavId = Exclude<Route, "appearance">;
const NAV: ReadonlyArray<{ id: NavId; label: string; Icon: ComponentType<{ size?: number }>; keywords: string }> = [
  { id: "today", label: "Today", Icon: IconToday, keywords: "home brief schedule plan calendar" },
  { id: "approvals", label: "Approvals", Icon: IconApprovals, keywords: "proposals queue approve reject" },
  { id: "work", label: "Work", Icon: IconWork, keywords: "prs pull requests reviews jira ci issues" },
  { id: "agents", label: "Agents", Icon: IconAgents, keywords: "runs schedule spend cost pause resume" },
  { id: "wellbeing", label: "Wellbeing", Icon: IconWellbeing, keywords: "mood checkin check-in load energy wins" },
  { id: "memory", label: "Memory", Icon: IconMemory, keywords: "search wiki notes people projects" },
  { id: "setup", label: "Setup", Icon: IconSetup, keywords: "onboarding accounts connectors vaults phone" },
];

const TITLES: Record<Route, string> = {
  ...(Object.fromEntries(NAV.map((n) => [n.id, n.label])) as Record<NavId, string>),
  appearance: "Appearance",
};

const SCREENS: Record<Route, ComponentType> = {
  today: TodayScreen,
  approvals: ApprovalsScreen,
  work: WorkScreen,
  agents: AgentsScreen,
  wellbeing: WellbeingScreen,
  memory: MemoryScreen,
  setup: SetupScreen,
  appearance: AppearanceScreen,
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
  const cls = variant === "nav" ? "nav-badge is-quiet" : "tab-badge is-quiet";
  if (d.complete) {
    return (
      <span className={`${cls} badge-check`} role="img" aria-label="Setup complete">
        <IconCheck size={variant === "nav" ? 11 : 10} strokeWidth={3} />
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

const NEXT_MODE: Record<ThemeMode, ThemeMode> = { system: "light", light: "dark", dark: "system" };

/** Cycles System → Light → Dark and saves it (the same pref the Appearance screen sets). */
function ModeButton() {
  const { snapshot, save } = useTheme();
  const toast = useToast();
  const pref = snapshot?.prefs.mode ?? "system";
  const Icon = pref === "light" ? IconSun : pref === "dark" ? IconMoon : IconSystem;
  const label = `Mode: ${pref}. Switch to ${NEXT_MODE[pref]}`;
  return (
    <button
      type="button"
      className="btn btn-icon btn-ghost"
      disabled={!snapshot}
      onClick={() => save({ mode: NEXT_MODE[pref] }).catch((e: unknown) => toast.show(e instanceof Error ? e.message : "Couldn't switch mode.", "error"))}
      aria-label={label}
      title={label}
    >
      <Icon size={16} />
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
  const { pendingCount, today, setup } = useAppData();
  const theme = useTheme();
  const toast = useToast();
  const online = !today.error && !!today.data;
  const [barOpen, setBarOpen] = useState(false);
  const [barMode, setBarMode] = useState<BarMode>("ask");

  const openBar = useCallback((mode?: BarMode) => {
    if (mode) setBarMode(mode);
    setBarOpen(true);
  }, []);

  const openAppearance = useCallback(() => go("appearance"), [go]);

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
    document.title = `${pendingCount ? `(${pendingCount}) ` : ""}${TITLES[route]} · me`;
  }, [route, pendingCount]);

  useEffect(() => {
    document.getElementById("main")?.scrollTo?.(0, 0);
    window.scrollTo(0, 0);
  }, [route]);

  const save = theme.save;
  const commands = useMemo<Command[]>(() => {
    const setMode = (mode: ThemeMode, label: string) => () =>
      save({ mode })
        .then(() => toast.show(label, "ok"))
        .catch((e: unknown) => toast.show(e instanceof Error ? e.message : "Couldn't switch mode.", "error"));
    return [
      ...NAV.map(({ id, label, Icon, keywords }) => ({
        id: `go-${id}`,
        label,
        hint: "Page",
        keywords,
        Icon,
        run: () => go(id),
      })),
      {
        id: "go-appearance",
        label: "Appearance",
        hint: "Theme, mode, accent, adaptive colours",
        keywords: "theme themes colours colors palette accent adaptive look dark light mode",
        Icon: IconPalette,
        run: () => go("appearance"),
      },
      { id: "mode-dark", label: "Dark mode", hint: "Appearance", keywords: "theme night", Icon: IconMoon, run: setMode("dark", "Dark mode on.") },
      { id: "mode-light", label: "Light mode", hint: "Appearance", keywords: "theme day", Icon: IconSun, run: setMode("light", "Light mode on.") },
      {
        id: "mode-system",
        label: "System mode",
        hint: "Follow your system's appearance",
        keywords: "theme auto automatic",
        Icon: IconSystem,
        run: setMode("system", "Following your system appearance."),
      },
    ];
  }, [go, save, toast]);

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
          <img src="./icon.svg" alt="" width={24} height={24} />
          <div className="brand-text">
            <div className="brand-name">me</div>
            <div className="brand-sub">Mission Control</div>
          </div>
        </div>
        <StatePill onAppearance={openAppearance} />
        <button type="button" className="cmd-trigger" onClick={() => openBar("ask")}>
          <IconCommand size={14} />
          <span>Ask or jump to…</span>
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
              <Icon size={16} />
              <span className="nav-label">{label}</span>
              {id === "approvals" && pendingCount > 0 ? (
                <span className="nav-badge" aria-label={`${pendingCount} pending`}>
                  {pendingCount > 99 ? "99+" : pendingCount}
                </span>
              ) : null}
              {id === "setup" ? <SetupBadge variant="nav" /> : null}
            </a>
          ))}
        </nav>
        <div className="side-foot">
          <span
            className="conn"
            title={today.updatedAt ? `Last updated ${today.updatedAt.toLocaleTimeString()}` : undefined}
          >
            <span className={`status-dot ${online ? "dot-ok" : today.loading ? "dot-dim" : "dot-bad"}`} aria-hidden="true" />
            {online ? "Connected" : today.loading ? "Connecting" : "Offline"}
          </span>
          <ModeButton />
          <a
            href="#/appearance"
            className={`btn btn-icon btn-ghost ${route === "appearance" ? "is-active" : ""}`}
            aria-label="Appearance"
            aria-current={route === "appearance" ? "page" : undefined}
            title="Appearance"
            onClick={(e) => {
              e.preventDefault();
              go("appearance");
            }}
          >
            <IconPalette size={16} />
          </a>
        </div>
      </aside>

      <header className="topbar">
        <div className="brand">
          <img src="./icon.svg" alt="" width={22} height={22} />
          <span className="brand-name">me</span>
        </div>
        <StatePill compact onAppearance={openAppearance} />
        <div className="topbar-actions">
          <a
            href="#/appearance"
            className={`btn btn-icon btn-ghost ${route === "appearance" ? "is-active" : ""}`}
            aria-label="Appearance"
            title="Appearance"
            onClick={(e) => {
              e.preventDefault();
              go("appearance");
            }}
          >
            <IconPalette size={17} />
          </a>
          <button type="button" className="btn btn-primary btn-sm" onClick={() => openBar("ask")}>
            <IconCommand size={13} /> Ask
          </button>
        </div>
      </header>

      <main id="main" className="main" tabIndex={-1}>
        <div className="main-inner">
          <ConnectionBanner />
          <PausedBanner />
          <Screen key={route} />
        </div>
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
              <Icon size={19} />
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

      <CommandBar
        open={barOpen}
        mode={barMode}
        onMode={setBarMode}
        onClose={() => setBarOpen(false)}
        commands={commands}
      />
    </div>
  );
}
