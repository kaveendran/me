import { Notice, TFile, getAllTags } from "obsidian";
import { succeeded, type MeRun } from "./cli";
import { diaryCallout, diarySectionName, todayISO } from "./format";
import { applyLinks, findFirstEligible, mergeTags, normalizeTag, sanitizeTarget } from "./links";
import {
  failureMessage,
  parseJsonOutput,
  parseLinksResult,
  parseSummaryResult,
  parseTasksResult,
  positional,
  type LinkSuggestion,
} from "./parse";
import { LinkPreviewModal, MarkdownModal, TaskPickerModal, progressNotice, showError, type LinkPreviewItem, type TagPreviewItem } from "./modals";
import type MeHarnessPlugin from "./main";

type Mode = "links" | "tasks" | "summary";

const ASK_PREFILL = "Summarise this note and tell me what's missing";

export function registerCommands(plugin: MeHarnessPlugin): void {
  plugin.addCommand({
    id: "open-assistant",
    name: "Open me assistant",
    callback: () => {
      void plugin.activateView().then((view) => view?.focusInput());
    },
  });

  const noteCommand = (id: string, name: string, action: (file: TFile) => Promise<void>) =>
    plugin.addCommand({
      id,
      name,
      checkCallback: (checking) => {
        const file = plugin.activeNote();
        if (!file) return false;
        if (!checking) void action(file);
        return true;
      },
    });

  noteCommand("ask-about-note", "Ask about this note", async () => {
    const view = await plugin.activateView();
    view?.prefill(ASK_PREFILL, true);
  });
  noteCommand("extract-tasks", "Extract tasks → today's inbox", (file) => extractTasks(plugin, file));
  noteCommand("suggest-links", "Suggest links & tags", (file) => suggestLinks(plugin, file));
  noteCommand("summarise-to-diary", "Summarise into today's diary", (file) => summariseToDiary(plugin, file));

  plugin.addCommand({
    id: "show-brief",
    name: "Show today's brief",
    callback: () => void showBrief(plugin),
  });
}

/** Runs one `me ask --json --mode …` call with a cancellable progress notice. Null on failure (already reported). */
async function askJson(plugin: MeHarnessPlugin, mode: Mode, file: TFile, message: string): Promise<{ value: unknown } | null> {
  const notePath = plugin.absolutePath(file);
  if (!notePath) {
    new Notice("me harness: this vault isn't on the local file system.");
    return null;
  }
  await plugin.flushEditor(file);
  const run = plugin.run(["ask", "--json", "--mode", mode, "--note", notePath]);
  const progress = progressNotice(message, () => run.stop());
  const result = await run.done;
  progress.hide();
  if (result.stopped) {
    new Notice("me: stopped.");
    return null;
  }
  if (!succeeded(result)) {
    showError(`me ask --mode ${mode} failed`, failureMessage(result));
    return null;
  }
  const parsed = parseJsonOutput(result.stdout);
  if (parsed === undefined) {
    showError(`me ask --mode ${mode}`, "me didn't print a JSON object.");
    return null;
  }
  return { value: parsed };
}

function parseOrReport<T>(title: string, parse: () => T): T | null {
  try {
    return parse();
  } catch (e) {
    showError(title, (e as Error).message);
    return null;
  }
}

async function extractTasks(plugin: MeHarnessPlugin, file: TFile): Promise<void> {
  const raw = await askJson(plugin, "tasks", file, `Finding tasks in ${file.basename}…`);
  if (raw === null) return;
  const parsed = parseOrReport("Extract tasks", () => parseTasksResult(raw.value));
  if (!parsed) return;
  if (parsed.tasks.length === 0) {
    new Notice(`No tasks found in ${file.basename}.`);
    return;
  }
  new TaskPickerModal(plugin.app, parsed.tasks, (tasks) => void addTasks(plugin, tasks)).open();
}

async function addTasks(plugin: MeHarnessPlugin, tasks: string[]): Promise<void> {
  let added = 0;
  const errors: string[] = [];
  // Sequential: each `me todo` appends to the same inbox file.
  for (const task of tasks) {
    const result = await plugin.run(["todo", ...positional(task)]).done;
    if (succeeded(result)) added++;
    else errors.push(failureMessage(result));
  }
  const noun = added === 1 ? "task" : "tasks";
  if (errors.length === 0) {
    new Notice(`Added ${added} ${noun} to today's inbox.`);
  } else {
    showError(`Added ${added} ${noun}; ${errors.length} failed`, errors[0]);
  }
}

async function suggestLinks(plugin: MeHarnessPlugin, file: TFile): Promise<void> {
  const raw = await askJson(plugin, "links", file, `Looking for links and tags in ${file.basename}…`);
  if (raw === null) return;
  const parsed = parseOrReport("Suggest links & tags", () => parseLinksResult(raw.value));
  if (!parsed) return;

  const { app } = plugin;
  // Read locally only, to preview which phrases can still be linked.
  const content = await app.vault.cachedRead(file);
  const links: LinkPreviewItem[] = parsed.links.map((link) => {
    const target = sanitizeTarget(link.target);
    return {
      link,
      found: target !== null && findFirstEligible(content, link.phrase) !== null,
      exists: target !== null && app.metadataCache.getFirstLinkpathDest(target, file.path) !== null,
    };
  });

  const cache = app.metadataCache.getFileCache(file);
  const present = new Set((cache ? getAllTags(cache) ?? [] : []).map((t) => t.replace(/^#/, "").toLowerCase()));
  const seenTags = new Set<string>();
  const tags: TagPreviewItem[] = [];
  for (const rawTag of parsed.tags) {
    const tag = normalizeTag(rawTag);
    if (!tag || seenTags.has(tag.toLowerCase())) continue;
    seenTags.add(tag.toLowerCase());
    tags.push({ tag, present: present.has(tag.toLowerCase()) });
  }

  if (links.length === 0 && tags.length === 0) {
    new Notice(`No link or tag suggestions for ${file.basename}.`);
    return;
  }
  new LinkPreviewModal(app, file.basename, links, tags, (chosenLinks, chosenTags) => {
    void applySuggestions(plugin, file, chosenLinks, chosenTags);
  }).open();
}

async function applySuggestions(plugin: MeHarnessPlugin, file: TFile, links: LinkSuggestion[], tags: string[]): Promise<void> {
  const { app } = plugin;
  let linked = 0;
  let missed = 0;
  let tagged = 0;
  try {
    if (links.length > 0) {
      // One vault.process call, so all link insertions land as a single change.
      await app.vault.process(file, (data) => {
        const result = applyLinks(data, links);
        linked = result.applied.length;
        missed = result.skipped.length;
        return result.text;
      });
    }
    if (tags.length > 0) {
      await app.fileManager.processFrontMatter(file, (frontmatter: Record<string, unknown>) => {
        const { tags: merged, added } = mergeTags(frontmatter.tags, tags, frontmatter.tag);
        if (added.length > 0) frontmatter.tags = merged;
        tagged = added.length;
      });
    }
  } catch (e) {
    showError("Couldn't update the note", (e as Error).message);
    return;
  }
  const parts = [`linked ${linked} phrase${linked === 1 ? "" : "s"}`];
  if (missed > 0) parts.push(`${missed} not found`);
  parts.push(`added ${tagged} tag${tagged === 1 ? "" : "s"}`);
  new Notice(`${file.basename}: ${parts.join(", ")}.`);
}

async function summariseToDiary(plugin: MeHarnessPlugin, file: TFile): Promise<void> {
  const raw = await askJson(plugin, "summary", file, `Summarising ${file.basename}…`);
  if (raw === null) return;
  const parsed = parseOrReport("Summarise into today's diary", () => parseSummaryResult(raw.value));
  if (!parsed) return;

  const now = new Date();
  const date = todayISO(now);
  const section = diarySectionName(now);
  const body = diaryCallout(file.basename, parsed.summary);

  new MarkdownModal(plugin.app, {
    title: "Summarise into today's diary",
    intro: `This goes into the "${section}" section of your ${date} diary.`,
    markdown: body,
    sourcePath: file.path,
    confirmText: "Add to diary",
    onConfirm: () => {
      void (async () => {
        const result = await plugin.run(["vault", "section", "diary", section, "--date", date], { stdin: body }).done;
        if (succeeded(result)) new Notice(`Summary of ${file.basename} added to the ${date} diary.`);
        else showError("Couldn't write to the diary", failureMessage(result));
      })();
    },
  }).open();
}

async function showBrief(plugin: MeHarnessPlugin): Promise<void> {
  let current: MeRun | null = null;
  let cancelled = false;
  const progress = progressNotice("Getting today's brief…", () => {
    cancelled = true;
    current?.stop();
  });
  try {
    // `me brief --if-stale && me today`, as two sequential spawns.
    current = plugin.run(["brief", "--if-stale"]);
    const brief = await current.done;
    if (cancelled || brief.stopped) return;
    if (!succeeded(brief)) {
      showError("me brief failed", failureMessage(brief));
      return;
    }
    current = plugin.run(["today"]);
    const today = await current.done;
    if (cancelled || today.stopped) return;
    if (!succeeded(today)) {
      showError("me today failed", failureMessage(today));
      return;
    }
    new MarkdownModal(plugin.app, { title: "Today's brief", markdown: today.stdout, sourcePath: "" }).open();
  } finally {
    progress.hide();
  }
}
