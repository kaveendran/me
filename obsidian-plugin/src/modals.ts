import { App, Component, MarkdownRenderer, Modal, Notice } from "obsidian";
import type { LinkSuggestion } from "./parse";

/** Adds a right-aligned button row; returns it. */
function buttonRow(parent: HTMLElement): HTMLElement {
  return parent.createDiv({ cls: "modal-button-container" });
}

function checkboxRow(parent: HTMLElement, checked: boolean, disabled: boolean): { row: HTMLLabelElement; box: HTMLInputElement } {
  const row = parent.createEl("label", { cls: "me-check-row" });
  const box = row.createEl("input", { type: "checkbox" });
  box.checked = checked;
  box.disabled = disabled;
  if (disabled) row.addClass("is-disabled");
  return { row, box };
}

/** "Extract tasks → today's inbox": pick which tasks to send to `me todo`. */
export class TaskPickerModal extends Modal {
  private readonly selected: boolean[];

  constructor(
    app: App,
    private readonly tasks: string[],
    private readonly onConfirm: (tasks: string[]) => void,
  ) {
    super(app);
    this.selected = tasks.map(() => true);
  }

  onOpen(): void {
    const { contentEl } = this;
    this.modalEl.addClass("me-modal");
    this.titleEl.setText("Extract tasks → today's inbox");
    contentEl.createEl("p", { cls: "me-modal-intro", text: "Each checked task is added to today's private inbox with me todo." });

    const list = contentEl.createDiv({ cls: "me-check-list" });
    const buttons = buttonRow(contentEl);
    const confirm = buttons.createEl("button", { cls: "mod-cta" });
    const cancel = buttons.createEl("button", { text: "Cancel" });

    const refresh = () => {
      const n = this.selected.filter(Boolean).length;
      confirm.setText(n === 1 ? "Add 1 task" : `Add ${n} tasks`);
      confirm.disabled = n === 0;
    };

    this.tasks.forEach((task, i) => {
      const { row, box } = checkboxRow(list, true, false);
      row.createSpan({ cls: "me-check-text", text: task });
      box.addEventListener("change", () => {
        this.selected[i] = box.checked;
        refresh();
      });
    });
    refresh();

    confirm.addEventListener("click", () => {
      const chosen = this.tasks.filter((_, i) => this.selected[i]);
      this.close();
      this.onConfirm(chosen);
    });
    cancel.addEventListener("click", () => this.close());
  }

  onClose(): void {
    this.contentEl.empty();
  }
}

export interface LinkPreviewItem {
  link: LinkSuggestion;
  /** An eligible occurrence exists in the note right now. */
  found: boolean;
  /** The target resolves to an existing note. */
  exists: boolean;
}

export interface TagPreviewItem {
  tag: string;
  /** Already on the note (frontmatter or inline). */
  present: boolean;
}

/** "Suggest links & tags": preview and choose which edits to apply. */
export class LinkPreviewModal extends Modal {
  constructor(
    app: App,
    private readonly noteName: string,
    private readonly links: LinkPreviewItem[],
    private readonly tags: TagPreviewItem[],
    private readonly onApply: (links: LinkSuggestion[], tags: string[]) => void,
  ) {
    super(app);
  }

  onOpen(): void {
    const { contentEl } = this;
    this.modalEl.addClass("me-modal");
    this.titleEl.setText("Suggest links & tags");
    contentEl.createEl("p", {
      cls: "me-modal-intro",
      text: `Changes to ${this.noteName}. Only the first unlinked occurrence of each phrase is linked; code, existing links and frontmatter are left alone.`,
    });

    const linkState = this.links.map((l) => l.found);
    const tagState = this.tags.map((t) => !t.present);

    const buttons = buttonRow(contentEl);
    const apply = buttons.createEl("button", { cls: "mod-cta" });
    const cancel = buttons.createEl("button", { text: "Cancel" });
    const refresh = () => {
      const n = linkState.filter(Boolean).length + tagState.filter(Boolean).length;
      apply.setText(n === 1 ? "Apply 1 change" : `Apply ${n} changes`);
      apply.disabled = n === 0;
    };

    const body = createDiv();
    contentEl.insertBefore(body, buttons);

    if (this.links.length) {
      body.createEl("h4", { cls: "me-modal-heading", text: "Links" });
      const list = body.createDiv({ cls: "me-check-list" });
      this.links.forEach((item, i) => {
        const { row, box } = checkboxRow(list, item.found, !item.found);
        const text = row.createSpan({ cls: "me-check-text" });
        text.createSpan({ cls: "me-phrase", text: item.link.phrase });
        text.createSpan({ cls: "me-arrow", text: " → " });
        text.createEl("code", { cls: "me-target", text: `[[${item.link.target}]]` });
        if (!item.found) text.createSpan({ cls: "me-hint", text: "not found in note" });
        else if (!item.exists) text.createSpan({ cls: "me-hint", text: "new note" });
        box.addEventListener("change", () => {
          linkState[i] = box.checked;
          refresh();
        });
      });
    }

    if (this.tags.length) {
      body.createEl("h4", { cls: "me-modal-heading", text: "Tags" });
      const list = body.createDiv({ cls: "me-check-list" });
      this.tags.forEach((item, i) => {
        const { row, box } = checkboxRow(list, !item.present, item.present);
        const text = row.createSpan({ cls: "me-check-text" });
        text.createEl("code", { cls: "me-tag", text: `#${item.tag}` });
        if (item.present) text.createSpan({ cls: "me-hint", text: "already on note" });
        box.addEventListener("change", () => {
          tagState[i] = box.checked;
          refresh();
        });
      });
    }
    refresh();

    apply.addEventListener("click", () => {
      const links = this.links.filter((_, i) => linkState[i]).map((l) => l.link);
      const tags = this.tags.filter((_, i) => tagState[i]).map((t) => t.tag);
      this.close();
      this.onApply(links, tags);
    });
    cancel.addEventListener("click", () => this.close());
  }

  onClose(): void {
    this.contentEl.empty();
  }
}

export interface MarkdownModalOptions {
  title: string;
  markdown: string;
  /** Resolves relative links in the rendered Markdown. */
  sourcePath: string;
  intro?: string;
  /** With onConfirm, shows a confirm button; otherwise just Close. */
  confirmText?: string;
  onConfirm?: () => void;
}

/** Renders Markdown with Obsidian's renderer; optionally asks for confirmation. */
export class MarkdownModal extends Modal {
  private readonly component = new Component();

  constructor(
    app: App,
    private readonly options: MarkdownModalOptions,
  ) {
    super(app);
  }

  onOpen(): void {
    const { contentEl, options } = this;
    this.modalEl.addClass("me-modal", "me-markdown-modal");
    this.titleEl.setText(options.title);
    this.component.load();
    if (options.intro) contentEl.createEl("p", { cls: "me-modal-intro", text: options.intro });

    const body = contentEl.createDiv({ cls: "me-markdown markdown-rendered" });
    if (options.markdown.trim()) {
      void MarkdownRenderer.render(this.app, options.markdown, body, options.sourcePath, this.component);
    } else {
      body.createEl("p", { cls: "me-hint", text: "Nothing to show." });
    }

    const buttons = buttonRow(contentEl);
    if (options.onConfirm) {
      const onConfirm = options.onConfirm;
      const confirm = buttons.createEl("button", { cls: "mod-cta", text: options.confirmText ?? "Confirm" });
      confirm.addEventListener("click", () => {
        this.close();
        onConfirm();
      });
      buttons.createEl("button", { text: "Cancel" }).addEventListener("click", () => this.close());
    } else {
      buttons.createEl("button", { text: "Close" }).addEventListener("click", () => this.close());
    }
  }

  onClose(): void {
    this.component.unload();
    this.contentEl.empty();
  }
}

/** A sticky notice for a running `me` call, with a Stop button. */
export function progressNotice(message: string, onStop: () => void): { hide: () => void } {
  const fragment = createFragment((frag) => {
    const wrap = frag.createDiv({ cls: "me-progress" });
    wrap.createSpan({ cls: "me-progress-dot" });
    wrap.createSpan({ text: message });
    const stop = wrap.createEl("button", { cls: "me-progress-stop", text: "Stop" });
    stop.addEventListener("click", (evt) => {
      evt.stopPropagation();
      onStop();
      notice.hide();
    });
  });
  const notice = new Notice(fragment, 0);
  return { hide: () => notice.hide() };
}

/** Shows a `me` failure (stderr or JSON error) in the UI. */
export function showError(title: string, message: string): void {
  const text = message.length > 1200 ? `${message.slice(0, 1200)}…` : message;
  const fragment = createFragment((frag) => {
    const wrap = frag.createDiv({ cls: "me-error-notice" });
    wrap.createEl("strong", { text: title });
    wrap.createDiv({ cls: "me-error-notice-body", text });
  });
  new Notice(fragment, 15000);
}
