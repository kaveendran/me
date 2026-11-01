// Runtime theme: fetches /api/theme, resolves light/dark, and applies the palette as
// CSS custom properties on <html>. The CSS in styles.css holds the first-paint
// defaults (Aurora); index.html replays the last applied palette from localStorage
// before React loads, so there's no flash.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  api,
  CHECKIN_EVENT,
  type Palette,
  type ResolvedMode,
  type ThemeApplied,
  type ThemeBody,
  type ThemeMode,
  type ThemeSnapshot,
} from "./api";

const POLL_MS = 60_000;
/** Focus + visibilitychange often fire together; don't fetch twice. */
const MIN_GAP_MS = 2_000;
/** Read by the inline script in index.html; keep the shape in sync. */
const CACHE_KEY = "me-theme-cache";
const SLOW = "1.2s"; // state changes morph slowly
const FAST = "0.3s"; // light/dark switches shouldn't drag

interface Cache {
  v: 1;
  mode: ThemeMode;
  palette: Record<ResolvedMode, Palette>;
  state: string;
  calm: boolean;
}

function readCache(): Cache | null {
  try {
    window.localStorage.removeItem("me-theme"); // the old browser-only light/dark switch

    const raw = window.localStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const c = JSON.parse(raw) as Partial<Cache>;
    if (c?.v !== 1 || !c.palette?.dark || !c.palette?.light) return null;
    return c as Cache;
  } catch {
    return null;
  }
}

function writeCache(s: ThemeSnapshot): void {
  try {
    const c: Cache = { v: 1, mode: s.prefs.mode, palette: s.palette, state: s.applied.id, calm: s.applied.calm };
    window.localStorage.setItem(CACHE_KEY, JSON.stringify(c));
  } catch {
    /* storage unavailable: the next load starts from the CSS defaults */
  }
}

const SYSTEM_DARK = "(prefers-color-scheme: dark)";
const KEY_RE = /^[a-z0-9-]{1,40}$/;

let animReady = false;

/** Write the palette onto <html>. Pure DOM; safe to call repeatedly. */
function applyToDom(p: Palette, mode: ResolvedMode, state: string, calm: boolean): void {
  const root = document.documentElement;
  const prevMode = root.dataset.mode;
  root.style.setProperty("--theme-dur", prevMode && prevMode !== mode ? FAST : SLOW);
  for (const [k, v] of Object.entries(p)) {
    if (KEY_RE.test(k) && typeof v === "string" && v.length < 64) root.style.setProperty(`--${k}`, v);
  }
  root.dataset.mode = mode;
  root.dataset.state = state;
  root.dataset.calm = calm ? "true" : "false";
  root.style.colorScheme = mode;
  const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (meta && p.bg) meta.content = p.bg;
  // Only morph after the first paint, so a fresh load never animates in.
  if (!animReady) {
    animReady = true;
    requestAnimationFrame(() => requestAnimationFrame(() => root.setAttribute("data-theme-anim", "")));
  }
}

export interface ThemeApi {
  /** The live snapshot (never a preview); null until the first response. */
  snapshot: ThemeSnapshot | null;
  error: Error | null;
  /** The light/dark actually showing. */
  mode: ResolvedMode;
  /** What `applied` describes right now: the preview while one is on, else live. */
  applied: ThemeApplied | null;
  /** State id being previewed (also set while its palette loads). */
  previewId: string | null;
  /** True while a POST /api/theme is in flight. */
  saving: boolean;
  refresh: () => Promise<void>;
  /** Optimistically applies, POSTs, and reverts on failure (then rethrows). */
  save: (body: ThemeBody) => Promise<void>;
  startPreview: (id: string) => Promise<void>;
  clearPreview: () => void;
}

const Ctx = createContext<ThemeApi | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const cache = useRef<Cache | null>(null);
  if (cache.current === null) cache.current = readCache();

  const [snap, setSnap] = useState<ThemeSnapshot | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [preview, setPreview] = useState<ThemeSnapshot | null>(null);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [systemDark, setSystemDark] = useState(() => window.matchMedia?.(SYSTEM_DARK).matches ?? true);

  const snapRef = useRef(snap);
  snapRef.current = snap;
  const previewRef = useRef<string | null>(null);
  const saves = useRef({ seq: 0, inFlight: 0 });
  const lastFetch = useRef(0);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  // ----- fetching -----

  const refresh = useCallback(async (force = false) => {
    const now = Date.now();
    if (!force && now - lastFetch.current < MIN_GAP_MS) return;
    if (saves.current.inFlight > 0) return; // the save's response is fresher
    lastFetch.current = now;
    const seq = saves.current.seq;
    try {
      const s = await api.theme();
      if (!alive.current || seq !== saves.current.seq || saves.current.inFlight > 0) return;
      setSnap(s);
      setError(null);
    } catch (e) {
      if (alive.current) setError(e instanceof Error ? e : new Error(String(e)));
    }
  }, []);

  useEffect(() => {
    void refresh(true);
    const id = window.setInterval(() => {
      if (!document.hidden) void refresh(true);
    }, POLL_MS);
    const onFocus = () => void refresh();
    const onVis = () => {
      if (!document.hidden) void refresh();
    };
    const onCheckin = () => void refresh(true);
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVis);
    window.addEventListener(CHECKIN_EVENT, onCheckin);
    return () => {
      window.clearInterval(id);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVis);
      window.removeEventListener(CHECKIN_EVENT, onCheckin);
    };
  }, [refresh]);

  useEffect(() => {
    const mq = window.matchMedia?.(SYSTEM_DARK);
    if (!mq) return;
    const on = () => setSystemDark(mq.matches);
    mq.addEventListener("change", on);
    on();
    return () => mq.removeEventListener("change", on);
  }, []);

  // ----- preview -----

  const loadPreview = useCallback(async (id: string) => {
    const p = await api.theme(id);
    if (alive.current && previewRef.current === id) setPreview(p);
  }, []);

  const startPreview = useCallback(
    async (id: string) => {
      previewRef.current = id;
      setPreviewId(id);
      try {
        await loadPreview(id);
      } catch (e) {
        if (previewRef.current === id) {
          previewRef.current = null;
          setPreviewId(null);
          setPreview(null);
        }
        throw e;
      }
    },
    [loadPreview],
  );

  const clearPreview = useCallback(() => {
    previewRef.current = null;
    setPreviewId(null);
    setPreview(null);
  }, []);

  // ----- saving -----

  const save = useCallback(
    async (body: ThemeBody) => {
      const before = snapRef.current;
      saves.current.seq++;
      saves.current.inFlight++;
      setSaving(true);
      if (before) setSnap(optimistic(before, body));
      let s: ThemeSnapshot;
      try {
        s = await api.saveTheme(body);
      } catch (e) {
        if (alive.current && before) setSnap(before);
        throw e;
      } finally {
        saves.current.inFlight--;
        if (saves.current.inFlight === 0 && alive.current) setSaving(false);
      }
      if (!alive.current) return;
      setSnap(s);
      setError(null);
      // A preview palette depends on the prefs too; re-resolve it.
      const pid = previewRef.current;
      if (pid) void loadPreview(pid).catch(() => undefined);
    },
    [loadPreview],
  );

  // ----- applying -----

  const pref: ThemeMode = snap?.prefs.mode ?? cache.current?.mode ?? "system";
  const mode: ResolvedMode = pref === "system" ? (systemDark ? "dark" : "light") : pref;
  const active = preview ?? snap;

  useLayoutEffect(() => {
    if (active) {
      applyToDom(active.palette[mode], mode, active.applied.id, active.applied.calm);
    } else if (cache.current) {
      applyToDom(cache.current.palette[mode], mode, cache.current.state, cache.current.calm);
    }
  }, [active, mode]);

  useEffect(() => {
    if (snap) writeCache(snap);
  }, [snap]);

  const value = useMemo<ThemeApi>(
    () => ({
      snapshot: snap,
      error,
      mode,
      applied: active?.applied ?? null,
      previewId,
      saving,
      refresh: () => refresh(true),
      save,
      startPreview,
      clearPreview,
    }),
    [snap, error, mode, active, previewId, saving, refresh, save, startPreview, clearPreview],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/**
 * Prefs change at once. When the theme's own look is showing (steady, no custom
 * accent), its swatches stand in for the palette until the server answers.
 */
function optimistic(s: ThemeSnapshot, body: ThemeBody): ThemeSnapshot {
  const prefs = { ...s.prefs, ...body };
  let palette = s.palette;
  const theme = body.name ? s.themes.find((t) => t.id === body.name) : undefined;
  if (theme && s.applied.id === "steady" && !prefs.accent) {
    const swap = (m: ResolvedMode): Palette => {
      const [bg, surface, accent, accent2] = theme.swatches[m];
      return { ...s.palette[m], ...(bg && { bg }), ...(surface && { surface }), ...(accent && { accent }), ...(accent2 && { "accent-2": accent2 }) };
    };
    palette = { dark: swap("dark"), light: swap("light") };
  }
  return { ...s, prefs, palette };
}

export function useTheme(): ThemeApi {
  const v = useContext(Ctx);
  if (!v) throw new Error("useTheme outside ThemeProvider");
  return v;
}
