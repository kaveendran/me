import { useCallback, useEffect, useRef, useState, type ComponentType, type CSSProperties, type ReactNode } from "react";
import { api, type Palette, type ResolvedMode, type ThemeBody, type ThemeMode } from "../api";
import { IconCheck, IconEye, IconMoon, IconPalette, IconSun, IconSystem } from "../components/Icons";
import { capFirst, liveTone, StateDot } from "../components/StateIndicator";
import { ErrorState, PageHead, SkeletonCard, Spinner, Switch } from "../components/ui";
import { useTheme } from "../theme";
import { useToast } from "../toast";

/** When each state applies (mirrors the table in docs/mission-control-api.md). */
const PRESETS: ReadonlyArray<{ name: string; hex: string }> = [
  { name: "Indigo", hex: "#6366f1" },
  { name: "Blue", hex: "#3b82f6" },
  { name: "Cyan", hex: "#06b6d4" },
  { name: "Green", hex: "#22c55e" },
  { name: "Amber", hex: "#f59e0b" },
  { name: "Orange", hex: "#f97316" },
  { name: "Rose", hex: "#f43f5e" },
  { name: "Violet", hex: "#8b5cf6" },
];

const MODES: ReadonlyArray<{ id: ThemeMode; label: string; Icon: ComponentType<{ size?: number }> }> = [
  { id: "system", label: "System", Icon: IconSystem },
  { id: "light", label: "Light", Icon: IconSun },
  { id: "dark", label: "Dark", Icon: IconMoon },
];

const swatchVar = (hex: string | undefined) => ({ ["--sw" as string]: hex ?? "transparent" }) as CSSProperties;

function Panel({
  title,
  desc,
  action,
  children,
  foot,
  id,
}: {
  title: string;
  desc?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  foot?: ReactNode;
  id: string;
}) {
  return (
    <section className="card panel" aria-labelledby={`${id}-h`}>
      <header className="panel-head">
        <div className="panel-head-text">
          <h2 id={`${id}-h`}>{title}</h2>
          {desc ? (
            <p className="panel-desc" id={`${id}-d`}>
              {desc}
            </p>
          ) : null}
        </div>
        {action}
      </header>
      <div className="panel-body">{children}</div>
      {foot ? <footer className="panel-foot">{foot}</footer> : null}
    </section>
  );
}

/** A tiny app mock in one theme's colours: [bg, surface, accent, accent-2]. */
function Mini({ sw, mode }: { sw: string[]; mode: ResolvedMode }) {
  const [bg, surface, accent, accent2] = sw;
  return (
    <span className={`mini mini-${mode}`} style={{ background: bg }} aria-hidden="true">
      <span className="mini-side" style={{ background: surface }}>
        <i style={{ background: accent }} />
        <i style={{ background: bg }} />
        <i style={{ background: bg }} />
      </span>
      <span className="mini-panel" style={{ background: surface }}>
        <i className="mini-bar" style={{ background: accent }} />
        <i className="mini-line" style={{ background: bg }} />
        <i className="mini-line short" style={{ background: bg }} />
        <i className="mini-dot" style={{ background: accent2 }} />
      </span>
    </span>
  );
}

/** Small swatch of one state's palette: bg, a surface tile and the accent. */
function StateSwatch({ p }: { p: Palette | undefined }) {
  if (!p) return <span className="state-swatch skeleton" aria-hidden="true" />;
  return (
    <span className="state-swatch" style={{ background: p.bg, borderColor: p.border }} aria-hidden="true">
      <i style={{ background: p.surface, borderColor: p.border }} />
      <b style={{ background: p.accent }} />
      <b style={{ background: p["accent-2"] }} />
    </span>
  );
}

export function AppearanceScreen() {
  const { snapshot, error, mode, previewId, saving, save, startPreview, clearPreview, refresh } = useTheme();
  const toast = useToast();
  const [statePalettes, setStatePalettes] = useState<Record<string, Record<ResolvedMode, Palette>>>({});
  const [loadingPreview, setLoadingPreview] = useState<string | null>(null);
  const [draftAccent, setDraftAccent] = useState<string | null>(null);
  const accentTimer = useRef<number | undefined>(undefined);
  const pendingAccent = useRef<string | null>(null);

  // Previews are temporary: leaving the screen goes back to live colours.
  useEffect(() => () => clearPreview(), [clearPreview]);

  const doSave = useCallback(
    async (body: ThemeBody) => {
      try {
        await save(body);
      } catch (e) {
        toast.show(e instanceof Error ? e.message : "Couldn't save that.", "error");
      }
    },
    [save, toast],
  );

  // Swatches for each state depend on the theme and accent. Fetch once saves have
  // landed, so an optimistic change never races the POST.
  const prefsKey = snapshot ? `${snapshot.prefs.name}|${snapshot.prefs.accent}` : "";
  const stateIds = snapshot?.states.map((s) => s.id).join(",") ?? "";
  useEffect(() => {
    if (!prefsKey || !stateIds || saving) return;
    const ctrl = new AbortController();
    Promise.all(stateIds.split(",").map((id) => api.theme(id, ctrl.signal).then((s) => [id, s.palette] as const)))
      .then((pairs) => setStatePalettes(Object.fromEntries(pairs)))
      .catch(() => undefined);
    return () => ctrl.abort();
  }, [prefsKey, stateIds, saving]);

  // Custom colour: the picker fires continuously while dragging, so save once it settles.
  const flushAccent = useCallback(() => {
    window.clearTimeout(accentTimer.current);
    const hex = pendingAccent.current;
    pendingAccent.current = null;
    if (hex) void doSave({ accent: hex });
  }, [doSave]);
  useEffect(() => () => flushAccent(), [flushAccent]);
  const onCustomAccent = (hex: string) => {
    setDraftAccent(hex);
    pendingAccent.current = hex.toLowerCase();
    window.clearTimeout(accentTimer.current);
    accentTimer.current = window.setTimeout(flushAccent, 350);
  };
  useEffect(() => setDraftAccent(null), [snapshot?.prefs.accent]);

  const preview = async (id: string) => {
    if (previewId === id) {
      clearPreview();
      return;
    }
    setLoadingPreview(id);
    try {
      await startPreview(id);
    } catch (e) {
      toast.show(e instanceof Error ? e.message : "Couldn't load that preview.", "error");
    } finally {
      setLoadingPreview(null);
    }
  };

  if (!snapshot) {
    return (
      <>
        <PageHead title="Appearance" sub="Theme, light or dark, and colours that follow your day." />
        {error ? (
          <ErrorState error={error} onRetry={() => void refresh()} />
        ) : (
          <div className="stack">
            <SkeletonCard rows={2} />
            <SkeletonCard rows={3} />
          </div>
        )}
      </>
    );
  }

  const { prefs, state: live, themes, states } = snapshot;
  const tone = liveTone(snapshot, !!previewId);
  const currentTheme = themes.find((t) => t.id === prefs.name) ?? themes[0];
  const themeAccent = currentTheme?.swatches[mode][2];
  const customAccent = draftAccent ?? prefs.accent;
  const isPreset = PRESETS.some((p) => p.hex === prefs.accent);
  const previewing = previewId ? states.find((s) => s.id === previewId) : undefined;
  const takeover = prefs.adaptive && live.id !== "steady";

  return (
    <div className="appearance">
      <PageHead title="Appearance" sub="Theme, light or dark, and colours that follow your day." />

      <section className="card now-card" aria-labelledby="now-h">
        <div className="now-main">
          <span className="now-icon" aria-hidden="true">
            <StateDot tone={tone} />
          </span>
          <div className="now-text">
            <h2 id="now-h" className="now-title">
              <span className="muted">Right now:</span> {live.label}
            </h2>
            <p className="now-msg">{live.message}</p>
          </div>
          <div className="now-load" aria-label={`Load ${Math.round(live.load)} of 100`}>
            <span className="now-load-num">{Math.round(live.load)}</span>
            <span className="now-load-label">load / 100</span>
          </div>
        </div>
        {live.reasons.length ? (
          <ul className="now-reasons" aria-label="Why">
            {live.reasons.map((r, i) => (
              <li key={i}>{capFirst(r)}</li>
            ))}
          </ul>
        ) : null}
        <p className="now-foot">
          {prefs.adaptive
            ? live.id === "steady"
              ? "Adaptive colours are on. It's a steady day, so your theme shows as chosen."
              : `Adaptive colours are on, so you're seeing ${live.label} colours.`
            : "Adaptive colours are off, so your theme's own colours show all day."}
        </p>
      </section>

      <Panel
        id="ap-theme"
        title="Theme"
        desc="The base palette. Every theme has a light and a dark version with the same contrast."
        foot={
          takeover ? (
            <>
              While <strong>{live.label}</strong> is on, its colours lead. Your theme shows in full on steady days.
            </>
          ) : undefined
        }
      >
        <div className="theme-grid" role="radiogroup" aria-label="Theme">
          {themes.map((t) => {
            const on = t.id === prefs.name;
            return (
              <button
                key={t.id}
                type="button"
                role="radio"
                aria-checked={on}
                className={`theme-card ${on ? "is-on" : ""}`}
                onClick={() => (on ? undefined : void doSave({ name: t.id }))}
              >
                <span className="theme-preview">
                  <Mini sw={t.swatches.dark} mode="dark" />
                  <Mini sw={t.swatches.light} mode="light" />
                </span>
                <span className="theme-meta">
                  <span className="theme-name">
                    {t.label}
                    {on ? (
                      <span className="theme-check" aria-hidden="true">
                        <IconCheck size={11} strokeWidth={3} />
                      </span>
                    ) : null}
                  </span>
                  <span className="theme-desc">{t.description}</span>
                </span>
              </button>
            );
          })}
        </div>
      </Panel>

      <div className="grid-2 appearance-pair">
        <Panel
          id="ap-mode"
          title="Mode"
          desc="Light, dark, or follow your system."
          foot={prefs.mode === "system" ? <>Following your system, which is {mode} right now.</> : undefined}
        >
          <div className="seg seg-lg" role="radiogroup" aria-label="Mode">
            {MODES.map(({ id, label, Icon }) => (
              <button
                key={id}
                type="button"
                role="radio"
                aria-checked={prefs.mode === id}
                className={prefs.mode === id ? "is-on" : ""}
                onClick={() => void doSave({ mode: id })}
              >
                <Icon size={15} />
                {label}
              </button>
            ))}
          </div>
        </Panel>

        <Panel
          id="ap-accent"
          title="Accent"
          desc="Your own accent colour, or the theme's."
          foot={
            prefs.adaptive
              ? "A custom accent shows on steady days. Other states bring their own colour."
              : "With adaptive colours off, your accent shows all day."
          }
        >
          <div className="swatches" role="radiogroup" aria-label="Accent colour">
            <button
              type="button"
              role="radio"
              aria-checked={!prefs.accent}
              className="swatch swatch-theme"
              style={swatchVar(themeAccent)}
              title={`Theme accent (${currentTheme?.label ?? "theme"})`}
              aria-label="Theme accent"
              onClick={() => (prefs.accent ? void doSave({ accent: "" }) : undefined)}
            />
            <span className="swatch-sep" aria-hidden="true" />
            {PRESETS.map((p) => (
              <button
                key={p.hex}
                type="button"
                role="radio"
                aria-checked={prefs.accent === p.hex}
                className="swatch"
                style={swatchVar(p.hex)}
                title={p.name}
                aria-label={p.name}
                onClick={() => void doSave({ accent: p.hex })}
              />
            ))}
            <label
              className={`swatch swatch-custom ${prefs.accent && !isPreset ? "is-on" : ""}`}
              style={swatchVar(prefs.accent && !isPreset ? customAccent : undefined)}
              title="Custom colour"
            >
              <span className="sr-only">Custom accent colour</span>
              <IconPalette size={14} />
              <input
                type="color"
                value={customAccent || themeAccent || "#6366f1"}
                onChange={(e) => onCustomAccent(e.target.value)}
                onBlur={flushAccent}
              />
            </label>
          </div>
          <div className="accent-row">
            <span className="accent-value">
              {prefs.accent ? (
                <>
                  <span className="mono">{customAccent}</span>
                  <span className="muted"> custom</span>
                </>
              ) : (
                <span className="muted">Using the {currentTheme?.label ?? "theme"} accent</span>
              )}
            </span>
            <button
              type="button"
              className="btn btn-sm"
              disabled={!prefs.accent}
              onClick={() => void doSave({ accent: "" })}
            >
              Use theme accent
            </button>
          </div>
        </Panel>
      </div>

      <Panel
        id="ap-adaptive"
        title="Adaptive colours"
        desc="Colours follow your workload and how you feel. Heavy days get calmer, not louder."
        action={
          <Switch
            checked={prefs.adaptive}
            onChange={(v) => void doSave({ adaptive: v })}
            label="Adaptive colours"
            describedBy="ap-adaptive-d"
          />
        }
      >
        <ul className="state-list">
          {states.map((st) => {
            const isLive = st.id === live.id;
            const isPrev = previewId === st.id;
            return (
              <li key={st.id} className={`state-row ${isPrev ? "is-previewing" : ""}`}>
                <StateSwatch p={statePalettes[st.id]?.[mode]} />
                <div className="state-row-body">
                  <div className="state-row-title">
                    <span>{st.label}</span>
                    {isLive ? <span className="tag tag-accent">Now</span> : null}
                    {st.calm ? <span className="tag">Calm</span> : null}
                  </div>
                  <p className="state-row-msg">{st.message}</p>
                  <p className="state-row-when">{st.when}</p>
                </div>
                <button
                  type="button"
                  className={`btn btn-sm ${isPrev ? "is-active" : ""}`}
                  aria-pressed={isPrev}
                  onClick={() => void preview(st.id)}
                  disabled={loadingPreview !== null && loadingPreview !== st.id}
                >
                  {loadingPreview === st.id ? <Spinner size={12} /> : <IconEye size={14} />}
                  {isPrev ? "Previewing" : "Preview"}
                </button>
              </li>
            );
          })}
        </ul>
      </Panel>

      {previewing ? (
        <div className="preview-chip" role="status">
          <StateDot tone="accent" />
          <span>
            Previewing <strong>{previewing.label}</strong>
          </span>
          <button type="button" className="btn btn-sm btn-primary" onClick={clearPreview}>
            Back to live
          </button>
        </div>
      ) : null}
    </div>
  );
}
