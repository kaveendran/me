# me harness for Obsidian

A sidebar assistant for Obsidian, backed by the local `me` CLI (which wraps Claude Code). The plugin never talks to a model itself. It spawns `me` and passes it the path of the current note, as described in [`../docs/obsidian-plugin.md`](../docs/obsidian-plugin.md).

**Desktop only.** The plugin needs Node's `child_process` to run `me`, so it doesn't load on mobile.

## Features

- **Sidebar chat.** Open it from the ribbon (bot icon) or the command "Open me assistant". You get:
  - streaming replies, rendered as Markdown, with tool-activity chips and a typing indicator;
  - an "Include current note" toggle, on by default, which passes `--note <absolute path>`;
  - conversation continuity through the returned `session_id`, and a New chat button that starts over;
  - Stop (or Esc) to interrupt, and the run's cost and duration in small print under each reply.
- **Commands** (in the command palette):
  - **Ask about this note**: opens the sidebar with "Summarise this note and tell me what's missing" ready to send.
  - **Extract tasks → today's inbox**: shows the tasks `me` found, all checked, then runs `me todo` for each one you keep.
  - **Suggest links & tags**: previews each suggested `phrase → [[target]]` link and tag, and applies only the ones you keep. Each phrase is linked once, at its first occurrence that isn't already a link. Frontmatter, code blocks, inline code, existing links, URLs, tags, comments and `<!-- me:x -->` marker regions are never edited. Links go in as one `vault.process` change, and tags go into the frontmatter `tags` list.
  - **Summarise into today's diary**: shows the summary, then writes it as a `> [!note] From [[Note]]` callout into the `note-HHMM` section of today's diary, through `me vault section`.
  - **Show today's brief**: runs `me brief --if-stale`, then `me today`, and shows the result.
- **Settings:**
  - the absolute path to `me`, with a Test button that runs `me --help`;
  - an extra PATH prefix, so `me` can find `claude` and `gh`. Obsidian started from the Dock, Start menu or a launcher doesn't inherit your shell PATH. The defaults point at `~/.local/bin`, where the installer puts `me` on every system.

## Privacy

Note content only reaches `me`, and only as a file path (`--note`). The plugin reads the note locally to preview and apply link edits. It never sends note content anywhere else, and it doesn't log note content or CLI output to the console.

## Build

```sh
npm install && npm run build
```

This type-checks the sources (TypeScript strict) and bundles `src/` into `main.js` at the plugin root. `main.js` is gitignored. `npm run dev` rebuilds on every change.

Run the unit tests for the pure functions (the stream parser and the link insertion) with:

```sh
npm test
```

## Install into a vault

1. Build the plugin (see above).
2. Symlink this folder into your vault's plugin directory:

   ```sh
   mkdir -p "<vault>/.obsidian/plugins"
   ln -s "$PWD" "<vault>/.obsidian/plugins/me-harness"
   ```

3. In Obsidian, open **Settings → Community plugins**. Turn off Restricted mode if it's on, then enable **me harness**. You may need to reload plugins first.
4. In **Settings → me harness**, check the path to `me` and press **Test**.

## Layout

| File | Purpose |
|---|---|
| `src/main.ts` | plugin entry point: view registration, ribbon, settings, process tracking |
| `src/view.ts` | the sidebar chat (`me-harness-view`) |
| `src/commands.ts` | the command palette actions |
| `src/modals.ts` | the task picker, link preview, Markdown and confirm modals, and notices |
| `src/settings.ts` | the settings tab |
| `src/cli.ts` | spawns `me` (no shell), line-buffers stdout, and handles Stop |
| `src/parse.ts` | pure: line buffer, stream-json events, one-shot JSON, error text |
| `src/links.ts` | pure: protected regions, first-occurrence link insertion, tag merging |
| `src/format.ts` | pure: dates, the diary callout, cost formatting |
| `test/pure.test.ts` | unit tests for the pure modules |
