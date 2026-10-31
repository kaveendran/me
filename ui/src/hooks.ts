import { useCallback, useEffect, useRef, useState } from "react";

/** Current time, re-rendering every `ms` (aligned to the next tick boundary). */
export function useNow(ms = 60_000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    let interval: number | undefined;
    const align = ms - (Date.now() % ms);
    const first = window.setTimeout(() => {
      setNow(new Date());
      interval = window.setInterval(() => setNow(new Date()), ms);
    }, align);
    return () => {
      window.clearTimeout(first);
      if (interval !== undefined) window.clearInterval(interval);
    };
  }, [ms]);
  return now;
}

export interface Resource<T> {
  data: T | null;
  error: Error | null;
  loading: boolean; // true only while there is no data yet
  refreshing: boolean;
  refresh: () => Promise<void>;
  /** Replace the data with a result fetched elsewhere; drops any older in-flight refresh. */
  set: (d: T) => void;
  updatedAt: Date | null;
}

/**
 * Fetch `fn` on mount, every `intervalMs` while the tab is visible, and on
 * demand. Keeps the last good data when a refresh fails.
 */
export function useResource<T>(
  fn: (signal: AbortSignal) => Promise<T>,
  intervalMs: number | null,
  deps: ReadonlyArray<unknown> = [],
): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [inFlight, setInFlight] = useState(0);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const ctrlRef = useRef<AbortController | null>(null);
  const seq = useRef(0);

  const refresh = useCallback(async () => {
    ctrlRef.current?.abort();
    const ctrl = new AbortController();
    ctrlRef.current = ctrl;
    const mine = ++seq.current;
    setInFlight((n) => n + 1);
    try {
      const d = await fnRef.current(ctrl.signal);
      if (mine === seq.current) {
        setData(d);
        setError(null);
        setUpdatedAt(new Date());
      }
    } catch (e) {
      if (!ctrl.signal.aborted && mine === seq.current) {
        setError(e instanceof Error ? e : new Error(String(e)));
      }
    } finally {
      setInFlight((n) => n - 1);
    }
  }, []);

  const set = useCallback((d: T) => {
    ctrlRef.current?.abort();
    seq.current++;
    setData(d);
    setError(null);
    setUpdatedAt(new Date());
  }, []);

  useEffect(() => {
    setData(null);
    setError(null);
    void refresh();
    return () => ctrlRef.current?.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    if (!intervalMs) return;
    const id = window.setInterval(() => {
      if (!document.hidden) void refresh();
    }, intervalMs);
    const onVis = () => {
      if (!document.hidden) void refresh();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [intervalMs, refresh]);

  return {
    data,
    error,
    loading: data === null && error === null,
    refreshing: inFlight > 0,
    refresh,
    set,
    updatedAt,
  };
}

/** Hash-based routing: "#/approvals" -> "approvals". */
export function useHashRoute<R extends string>(routes: readonly R[], fallback: R): [R, (r: R) => void] {
  const read = useCallback((): R => {
    const h = window.location.hash.replace(/^#\/?/, "").split(/[?/]/)[0] ?? "";
    return (routes as readonly string[]).includes(h) ? (h as R) : fallback;
  }, [routes, fallback]);
  const [route, setRoute] = useState<R>(read);
  useEffect(() => {
    const on = () => setRoute(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, [read]);
  const go = useCallback((r: R) => {
    if (window.location.hash !== `#/${r}`) window.location.hash = `/${r}`;
    setRoute(r);
  }, []);
  return [route, go];
}

export type ThemePref = "system" | "light" | "dark";
const THEME_KEY = "me-theme";

function readTheme(): ThemePref {
  try {
    const v = window.localStorage.getItem(THEME_KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

export function useTheme(): [ThemePref, () => void] {
  const [pref, setPref] = useState<ThemePref>(readTheme);
  useEffect(() => {
    const root = document.documentElement;
    if (pref === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", pref);
    try {
      if (pref === "system") window.localStorage.removeItem(THEME_KEY);
      else window.localStorage.setItem(THEME_KEY, pref);
    } catch {
      /* storage unavailable: the choice lasts for this session only */
    }
  }, [pref]);
  const cycle = useCallback(() => {
    setPref((p) => (p === "system" ? "light" : p === "light" ? "dark" : "system"));
  }, []);
  return [pref, cycle];
}

/** True when the viewport matches the media query. */
export function useMedia(query: string): boolean {
  const [match, setMatch] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setMatch(mq.matches);
    mq.addEventListener("change", on);
    on();
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return match;
}
