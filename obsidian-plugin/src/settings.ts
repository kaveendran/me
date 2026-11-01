import { App, PluginSettingTab, Setting } from "obsidian";
import * as os from "os";
import * as path from "path";
import { succeeded } from "./cli";
import { failureMessage } from "./parse";
import type MeHarnessPlugin from "./main";

export interface MeSettings {
  /** Absolute path to the `me` CLI. Obsidian started from the Dock, Start menu or a launcher doesn't inherit the shell PATH. */
  mePath: string;
  /** Prepended to PATH for spawned processes so `me` can find `claude` and `gh`. */
  pathPrefix: string;
}

/** Where the installer puts `me` (and uv, gh, claude): ~/.local/bin on every system. */
const LOCAL_BIN = path.join(os.homedir(), ".local", "bin");

export const DEFAULT_SETTINGS: MeSettings = {
  mePath: path.join(LOCAL_BIN, process.platform === "win32" ? "me.exe" : "me"),
  pathPrefix: [LOCAL_BIN, ...(process.platform === "darwin" ? ["/opt/homebrew/bin", "/usr/local/bin"] : [])].join(path.delimiter),
};

export class MeSettingTab extends PluginSettingTab {
  constructor(app: App, private readonly plugin: MeHarnessPlugin) {
    super(app, plugin);
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();

    let status: HTMLElement | null = null;
    const setting = new Setting(containerEl)
      .setName("Path to me")
      .setDesc("Absolute path to the me CLI. Obsidian started from the Dock, Start menu or a launcher doesn't see your shell PATH.")
      .addText((text) =>
        text
          .setPlaceholder(DEFAULT_SETTINGS.mePath)
          .setValue(this.plugin.settings.mePath)
          .onChange(async (value) => {
            this.plugin.settings.mePath = value.trim();
            await this.plugin.saveSettings();
          }),
      )
      .addButton((button) =>
        button.setButtonText("Test").onClick(async () => {
          if (!status) return;
          const mePath = this.plugin.settings.mePath;
          if (!path.isAbsolute(mePath)) {
            this.showStatus(status, false, "Use an absolute path, for example " + DEFAULT_SETTINGS.mePath);
            return;
          }
          button.setDisabled(true);
          this.showStatus(status, null, "Running me --help…");
          const result = await this.plugin.run(["--help"]).done;
          button.setDisabled(false);
          if (succeeded(result)) this.showStatus(status, true, "OK");
          else this.showStatus(status, false, failureMessage(result));
        }),
      );
    status = setting.descEl.createDiv({ cls: "me-settings-status" });

    new Setting(containerEl)
      .setName("Extra PATH prefix")
      .setDesc(`Prepended to PATH for spawned processes, so me can find claude and gh. Separate folders with ${path.delimiter}`)
      .addText((text) =>
        text
          .setPlaceholder(DEFAULT_SETTINGS.pathPrefix)
          .setValue(this.plugin.settings.pathPrefix)
          .onChange(async (value) => {
            this.plugin.settings.pathPrefix = value.trim();
            await this.plugin.saveSettings();
          }),
      );
  }

  private showStatus(el: HTMLElement, ok: boolean | null, message: string): void {
    el.empty();
    el.toggleClass("is-ok", ok === true);
    el.toggleClass("is-error", ok === false);
    el.setText(message);
  }
}
