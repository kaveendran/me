import { FileSystemAdapter, MarkdownView, Plugin, TFile, WorkspaceLeaf } from "obsidian";
import * as path from "path";
import { runMe, type MeRun, type RunOptions } from "./cli";
import { registerCommands } from "./commands";
import { DEFAULT_SETTINGS, MeSettingTab, type MeSettings } from "./settings";
import { MeView, VIEW_TYPE_ME } from "./view";

export default class MeHarnessPlugin extends Plugin {
  settings: MeSettings = { ...DEFAULT_SETTINGS };
  private readonly runs = new Set<MeRun>();

  async onload(): Promise<void> {
    await this.loadSettings();

    this.registerView(VIEW_TYPE_ME, (leaf) => new MeView(leaf, this));
    this.registerHoverLinkSource(VIEW_TYPE_ME, { display: "me harness", defaultMod: true });
    this.addRibbonIcon("bot", "Open me assistant", () => {
      void this.activateView();
    });
    registerCommands(this);
    this.addSettingTab(new MeSettingTab(this.app, this));
  }

  onunload(): void {
    for (const run of this.runs) run.stop();
    this.runs.clear();
  }

  async loadSettings(): Promise<void> {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, (await this.loadData()) as Partial<MeSettings> | null);
  }

  async saveSettings(): Promise<void> {
    await this.saveData(this.settings);
  }

  /** Spawns `me` with the configured path and PATH prefix; the run is stopped if the plugin unloads. */
  run(args: string[], options?: RunOptions): MeRun {
    const run = runMe(this.settings, args, options);
    this.runs.add(run);
    void run.done.then(() => this.runs.delete(run));
    return run;
  }

  /** Opens (or reveals) the assistant in the right sidebar. */
  async activateView(): Promise<MeView | null> {
    const { workspace } = this.app;
    let leaf: WorkspaceLeaf | null = workspace.getLeavesOfType(VIEW_TYPE_ME)[0] ?? null;
    if (!leaf) {
      leaf = workspace.getRightLeaf(false);
      if (!leaf) return null;
      await leaf.setViewState({ type: VIEW_TYPE_ME, active: true });
    }
    await workspace.revealLeaf(leaf);
    return leaf.view instanceof MeView ? leaf.view : null;
  }

  /** The active Markdown file, if any. Works while the sidebar has focus. */
  activeNote(): TFile | null {
    const file = this.app.workspace.getActiveFile();
    return file && file.extension === "md" ? file : null;
  }

  /** Absolute filesystem path of a vault file, for `--note`. */
  absolutePath(file: TFile): string | null {
    const adapter = this.app.vault.adapter;
    if (!(adapter instanceof FileSystemAdapter)) return null;
    return path.join(adapter.getBasePath(), file.path);
  }

  /** Saves any open editor for this file so `me` reads what the user sees. */
  async flushEditor(file: TFile): Promise<void> {
    for (const leaf of this.app.workspace.getLeavesOfType("markdown")) {
      const view = leaf.view;
      if (view instanceof MarkdownView && view.file?.path === file.path) {
        try {
          await view.save();
        } catch {
          /* autosave will catch up; me reads whatever is on disk */
        }
      }
    }
  }
}
