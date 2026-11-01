# me · Mission Control (UI)

Local dashboard for the `me` harness: Today, Approvals, Work, Agents, Wellbeing, Memory, Appearance, a first-run Setup screen and a ⌘K command bar.
Vite + React 18 + TypeScript, no UI framework. The API contract is `../docs/mission-control-api.md`.

## Theme

Colours come from `GET /api/theme` (the palette engine is `harness/theme.py`). `src/theme.tsx` applies the
palette for the resolved light/dark mode as CSS custom properties on `<html>` (`bg` → `--bg`), sets
`data-mode`, `data-state` and `data-calm`, and caches the last palette in localStorage so `index.html` can
replay it before first paint. `src/styles.css` holds the Aurora defaults and builds every other colour from
those tokens; the main ones are registered with `@property` so state changes morph. `data-calm="true"`
(strained, rest) drops glow and motion and quiets badges. Don't hard-code colours outside the token block.

## Build

```sh
npm install
npm run build        # tsc --noEmit && vite build -> dist/
```

`me serve` serves `ui/dist/` on http://127.0.0.1:4777 and replaces the `<!--ME_TOKEN-->` comment in
`index.html` with `<meta name="me-token" …>`. Asset paths are relative (`base: "./"`), so keep that comment
inside `<head>` of `index.html`.

## Develop

Run `me serve` in another terminal, then:

```sh
VITE_ME_TOKEN="$(cat ../state/server_token)" npm run dev
```

The Vite dev server proxies `/api` to `http://127.0.0.1:4777` and rewrites `Host`, so the server's host
check passes. In dev there's no injected meta tag, so the token comes from `VITE_ME_TOKEN`.

## Auth

Every `/api/*` call sends `X-Me-Token`. The SSE chat (`/api/chat`) passes `?token=` because EventSource can't
set headers. See `src/api.ts`.
