import { App, Component, ItemView, Keymap, MarkdownRenderer, WorkspaceLeaf, setIcon } from "obsidian";
import type { MeRun } from "./cli";
import { formatCost, formatDuration } from "./format";
import { failureMessage, parseStreamLine, positional, type ToolUse } from "./parse";
import type MeHarnessPlugin from "./main";

export const VIEW_TYPE_ME = "me-harness-view";

const TOOL_ICONS: Record<string, string> = {
  Read: "file-text",
  Write: "file-plus",
  Edit: "pencil",
  MultiEdit: "pencil",
  Glob: "folder-search",
  Grep: "search",
  Bash: "terminal",
  WebFetch: "globe",
  WebSearch: "globe",
  Task: "users",
  Agent: "users",
  TodoWrite: "list-checks",
};

function toolIcon(name: string): string {
  if (name.startsWith("mcp__")) return "plug";
  return TOOL_ICONS[name] ?? "wrench";
}

/** One assistant message: tool chips, streamed Markdown, typing indicator, cost. */
class AssistantReply extends Component {
  readonly el: HTMLElement;
  private readonly chipsEl: HTMLElement;
  private readonly bodyEl: HTMLElement;
  private readonly typingEl: HTMLElement;
  private readonly footerEl: HTMLElement;
  private text = "";
  private renderSeq = 0;
  private renderTimer: number | null = null;
  private rendered: Component | null = null;
  private errorEl: HTMLElement | null = null;
  private chips: HTMLElement[] = [];

  constructor(
    private readonly app: App,
    parent: HTMLElement,
    private readonly sourcePath: string,
    private readonly onChange: () => void,
  ) {
    super();
    this.el = parent.createDiv({ cls: "me-msg me-msg-assistant" });
    this.el.dataset.sourcePath = sourcePath;
    this.chipsEl = this.el.createDiv({ cls: "me-chips" });
    this.bodyEl = this.el.createDiv({ cls: "me-msg-body markdown-rendered" });
    this.typingEl = this.el.createDiv({ cls: "me-typing", attr: { "aria-label": "me is thinking" } });
    for (let i = 0; i < 3; i++) this.typingEl.createSpan();
    this.footerEl = this.el.createDiv({ cls: "me-msg-footer" });
  }

  get hasText(): boolean {
    return this.text.trim().length > 0;
  }

  appendText(chunk: string): void {
    if (!chunk) return;
    if (this.text && !this.text.endsWith("\n\n")) this.text += this.text.endsWith("\n") ? "\n" : "\n\n";
    this.text += chunk;
    this.scheduleRender();
  }

  /** Used when the result event carries text that never arrived as assistant events. */
  setTextIfEmpty(text: string): void {
    if (!this.hasText && text.trim()) {
      this.text = text;
      this.scheduleRender(0);
    }
  }

  addTool(tool: ToolUse): void {
    const chip = this.chipsEl.createDiv({ cls: "me-chip is-running" });
    setIcon(chip.createSpan({ cls: "me-chip-icon" }), toolIcon(tool.name));
    chip.createSpan({ cls: "me-chip-label", text: tool.label });
    if (tool.detail) chip.createSpan({ cls: "me-chip-detail", text: tool.detail });
    chip.setAttr("aria-label", tool.detail ? `${tool.label}: ${tool.detail}` : tool.label);
    this.chips.push(chip);
    this.onChange();
  }

  complete(costUsd?: number, durationMs?: number): void {
    this.settle();
    const parts: string[] = [];
    if (costUsd !== undefined) parts.push(formatCost(costUsd));
    if (durationMs !== undefined) parts.push(formatDuration(durationMs));
    if (parts.length) this.footerEl.setText(parts.join(" · "));
  }

  fail(message: string): void {
    this.settle();
    if (!this.errorEl) {
      this.errorEl = this.el.createDiv({ cls: "me-msg-error" });
      this.el.insertBefore(this.errorEl, this.footerEl);
    } else if (this.errorEl.getText().includes(message)) {
      return;
    } else {
      this.errorEl.appendText("\n");
    }
    this.errorEl.appendText(message);
    this.onChange();
  }

  markStopped(): void {
    this.settle();
    this.footerEl.setText("Stopped");
  }

  /** Stop the typing indicator and chip animation, and flush any pending render. */
  private settle(): void {
    this.typingEl.remove();
    this.chips.forEach((c) => c.removeClass("is-running"));
    if (this.renderTimer !== null) this.scheduleRender(0);
    if (!this.hasText && this.chips.length === 0) this.bodyEl.addClass("is-empty");
  }

  private scheduleRender(delay = 60): void {
    if (this.renderTimer !== null) window.clearTimeout(this.renderTimer);
    this.renderTimer = window.setTimeout(() => {
      this.renderTimer = null;
      void this.render();
    }, delay);
  }

  private async render(): Promise<void> {
    const seq = ++this.renderSeq;
    const component = new Component();
    this.addChild(component);
    const holder = createDiv();
    await MarkdownRenderer.render(this.app, this.text, holder, this.sourcePath, component);
    if (seq !== this.renderSeq || !this.el.isConnected) {
      this.removeChild(component);
      return;
    }
    this.bodyEl.empty();
    this.bodyEl.append(...Array.from(holder.childNodes));
    if (this.rendered) this.removeChild(this.rendered);
    this.rendered = component;
    this.onChange();
  }

  onunload(): void {
    if (this.renderTimer !== null) window.clearTimeout(this.renderTimer);
  }
}

interface ActiveTurn {
  run: MeRun;
  reply: AssistantReply;
}

export class MeView extends ItemView {
  private sessionId: string | null = null;
  private includeNote = true;
  private active: ActiveTurn | null = null;
  private replies: AssistantReply[] = [];
  /** Bumped by New chat, so a run that is still winding down can't touch the new conversation. */
  private conversation = 0;

  private messagesEl!: HTMLElement;
  private inputEl!: HTMLTextAreaElement;
  private sendBtn!: HTMLButtonElement;
  private noteToggle!: HTMLInputElement;
  private noteNameEl!: HTMLElement;
  private sessionEl!: HTMLElement;
  private stickToBottom = true;

  constructor(
    leaf: WorkspaceLeaf,
    private readonly plugin: MeHarnessPlugin,
  ) {
    super(leaf);
  }

  getViewType(): string {
    return VIEW_TYPE_ME;
  }

  getDisplayText(): string {
    return "me assistant";
  }

  getIcon(): string {
    return "bot";
  }

  async onOpen(): Promise<void> {
    const root = this.contentEl;
    root.empty();
    root.addClass("me-harness");

    const header = root.createDiv({ cls: "me-header" });
    const titles = header.createDiv({ cls: "me-header-titles" });
    titles.createDiv({ cls: "me-header-title", text: "me" });
    this.sessionEl = titles.createDiv({ cls: "me-header-session" });
    const newChat = header.createEl("button", { cls: "clickable-icon me-icon-btn", attr: { "aria-label": "New chat" } });
    setIcon(newChat, "plus");
    this.registerDomEvent(newChat, "click", () => this.newChat());

    this.messagesEl = root.createDiv({ cls: "me-messages" });
    this.registerDomEvent(this.messagesEl, "scroll", () => {
      const el = this.messagesEl;
      this.stickToBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
    });
    this.registerDomEvent(this.messagesEl, "click", (evt) => this.onLinkClick(evt));
    this.registerDomEvent(this.messagesEl, "mouseover", (evt) => this.onLinkHover(evt));

    const composer = root.createDiv({ cls: "me-composer" });
    const context = composer.createEl("label", { cls: "me-context" });
    this.noteToggle = context.createEl("input", { type: "checkbox" });
    this.noteToggle.checked = this.includeNote;
    context.createSpan({ cls: "me-context-label", text: "Include current note" });
    this.noteNameEl = context.createSpan({ cls: "me-context-note" });
    this.registerDomEvent(this.noteToggle, "change", () => {
      this.includeNote = this.noteToggle.checked;
      this.refreshContext();
    });

    const inputRow = composer.createDiv({ cls: "me-input-row" });
    this.inputEl = inputRow.createEl("textarea", {
      cls: "me-input",
      attr: { rows: "1", placeholder: "Ask me…  (Enter to send, Shift+Enter for a new line)" },
    });
    this.sendBtn = inputRow.createEl("button", { cls: "me-send mod-cta" });
    this.registerDomEvent(this.inputEl, "input", () => this.autosize());
    this.registerDomEvent(this.inputEl, "keydown", (evt) => {
      if (evt.key === "Enter" && !evt.shiftKey && !evt.isComposing) {
        evt.preventDefault();
        void this.send();
      } else if (evt.key === "Escape" && this.active) {
        evt.preventDefault();
        this.stop();
      }
    });
    this.registerDomEvent(this.sendBtn, "click", () => {
      if (this.active) this.stop();
      else void this.send();
    });

    this.registerEvent(this.app.workspace.on("active-leaf-change", () => this.refreshContext()));
    this.registerEvent(this.app.workspace.on("file-open", () => this.refreshContext()));
    this.registerEvent(this.app.vault.on("rename", () => this.refreshContext()));

    this.renderEmptyState();
    this.refreshContext();
    this.refreshSession();
    this.setRunning(false);
  }

  async onClose(): Promise<void> {
    this.active?.run.stop();
    this.active = null;
  }

  /** Puts text in the input and focuses it (used by "Ask about this note"). */
  prefill(text: string, includeNote = true): void {
    if (!this.inputEl) return;
    this.includeNote = includeNote;
    this.noteToggle.checked = includeNote;
    this.refreshContext();
    this.inputEl.value = text;
    this.autosize();
    this.focusInput();
    this.inputEl.setSelectionRange(text.length, text.length);
  }

  focusInput(): void {
    window.setTimeout(() => this.inputEl?.focus(), 0);
  }

  newChat(): void {
    this.stop();
    this.active = null;
    this.conversation++;
    this.setRunning(false);
    this.sessionId = null;
    for (const reply of this.replies) this.removeChild(reply);
    this.replies = [];
    this.messagesEl.empty();
    this.renderEmptyState();
    this.refreshSession();
    this.focusInput();
  }

  private stop(): void {
    this.active?.run.stop();
  }

  private async send(): Promise<void> {
    const question = this.inputEl.value.trim();
    if (!question || this.active) return;
    // Clear before any await, so a second Enter can't submit the same question twice.
    this.inputEl.value = "";
    this.autosize();

    const args = ["ask", "--stream"];
    let sourcePath = "";
    let noteLabel: string | null = null;
    const file = this.includeNote ? this.plugin.activeNote() : null;
    if (file) {
      const abs = this.plugin.absolutePath(file);
      if (abs) {
        await this.plugin.flushEditor(file);
        args.push("--note", abs);
        sourcePath = file.path;
        noteLabel = file.basename;
      }
    }
    if (this.sessionId) args.push("--session", this.sessionId);
    args.push(...positional(question));

    this.messagesEl.querySelector(".me-empty")?.remove();
    this.stickToBottom = true;

    const userEl = this.messagesEl.createDiv({ cls: "me-msg me-msg-user" });
    userEl.createDiv({ cls: "me-msg-text", text: question });
    if (noteLabel) {
      const ctx = userEl.createDiv({ cls: "me-msg-context" });
      setIcon(ctx.createSpan({ cls: "me-msg-context-icon" }), "file-text");
      ctx.createSpan({ text: noteLabel });
    }

    const reply = new AssistantReply(this.app, this.messagesEl, sourcePath, () => this.scrollToBottom());
    this.addChild(reply);
    this.replies.push(reply);
    this.scrollToBottom();

    let gotResult = false;
    const conversation = this.conversation;
    const run = this.plugin.run(args, {
      onStdoutLine: (line) => {
        const event = parseStreamLine(line);
        if (!event) return;
        if (event.type === "assistant") {
          if (!event.nested) event.texts.forEach((t) => reply.appendText(t));
          event.tools.forEach((t) => reply.addTool(t));
        } else {
          gotResult = true;
          if (event.sessionId && conversation === this.conversation) {
            this.sessionId = event.sessionId;
            this.refreshSession();
          }
          if (event.ok) {
            reply.setTextIfEmpty(event.result);
            reply.complete(event.costUsd, event.durationMs);
          } else {
            reply.fail(event.result.trim() || `me ask ended with ${event.subtype || "an error"}`);
            if (event.costUsd !== undefined) reply.complete(event.costUsd, event.durationMs);
          }
        }
      },
    });
    this.active = { run, reply };
    this.setRunning(true);

    const result = await run.done;
    if (this.active?.run === run) {
      this.active = null;
      this.setRunning(false);
    }

    if (result.stopped) reply.markStopped();
    else if (result.spawnError || result.code !== 0) reply.fail(failureMessage(result));
    else if (!gotResult) reply.complete();
  }

  private setRunning(running: boolean): void {
    this.sendBtn.empty();
    setIcon(this.sendBtn, running ? "square" : "arrow-up");
    this.sendBtn.setAttr("aria-label", running ? "Stop (Esc)" : "Send");
    this.sendBtn.toggleClass("is-stop", running);
    this.sendBtn.toggleClass("mod-cta", !running);
  }

  private refreshContext(): void {
    if (!this.noteNameEl) return;
    const file = this.plugin.activeNote();
    this.noteNameEl.setText(file ? file.basename : "no note open");
    this.noteNameEl.toggleClass("is-missing", !file);
    this.noteNameEl.toggleClass("is-off", !this.includeNote);
  }

  private refreshSession(): void {
    this.sessionEl.setText(this.sessionId ? "Continuing conversation" : "New conversation");
    if (this.sessionId) this.sessionEl.setAttr("aria-label", `Session ${this.sessionId}`);
    else this.sessionEl.removeAttribute("aria-label");
  }

  private renderEmptyState(): void {
    const empty = this.messagesEl.createDiv({ cls: "me-empty" });
    setIcon(empty.createDiv({ cls: "me-empty-icon" }), "bot");
    empty.createDiv({ text: "Ask about the current note, your day, or anything me can reach." });
  }

  private autosize(): void {
    this.inputEl.style.height = "auto";
    this.inputEl.style.height = `${Math.min(this.inputEl.scrollHeight, 200)}px`;
  }

  private scrollToBottom(): void {
    if (!this.stickToBottom) return;
    this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
  }

  private linkTarget(evt: MouseEvent): { el: HTMLAnchorElement; href: string; sourcePath: string } | null {
    const target = evt.target;
    if (!(target instanceof HTMLElement)) return null;
    const el = target.closest<HTMLAnchorElement>("a.internal-link");
    if (!el) return null;
    const href = el.getAttr("data-href") ?? el.getAttr("href");
    if (!href) return null;
    const msg = el.closest<HTMLElement>(".me-msg");
    return { el, href, sourcePath: msg?.dataset.sourcePath ?? "" };
  }

  private onLinkClick(evt: MouseEvent): void {
    const link = this.linkTarget(evt);
    if (!link) return;
    evt.preventDefault();
    void this.app.workspace.openLinkText(link.href, link.sourcePath, Keymap.isModEvent(evt));
  }

  private onLinkHover(evt: MouseEvent): void {
    const link = this.linkTarget(evt);
    if (!link) return;
    this.app.workspace.trigger("hover-link", {
      event: evt,
      source: VIEW_TYPE_ME,
      hoverParent: this,
      targetEl: link.el,
      linktext: link.href,
      sourcePath: link.sourcePath,
    });
  }
}
