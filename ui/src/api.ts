// Typed client for the Mission Control API (docs/mission-control-api.md).
// Every /api/* call sends X-Me-Token; the SSE chat endpoint takes ?token=.

// ---------- Auth ----------

function readToken(): string {
  const meta = document.querySelector<HTMLMetaElement>('meta[name="me-token"]');
  const fromMeta = meta?.content?.trim();
  if (fromMeta) return fromMeta;
  return (import.meta.env.VITE_ME_TOKEN ?? "").trim();
}

export const token: string = readToken();
export const hasToken = token.length > 0;

// ---------- Shapes ----------

export interface CalEvent {
  id: string;
  title: string;
  start: string; // "HH:MM"
  end: string; // "HH:MM"
  attendees: number;
  meet_url: string;
}

export interface FocusWindow {
  start: string;
  end: string;
  minutes: number;
}

export interface ProposedTask {
  title: string;
  why: string;
  ref: string;
  est_min: number;
  source: string;
}

export interface PlanItem {
  title: string;
  done: boolean;
}

export interface PlanBlock {
  start: string;
  end: string;
  task: string;
}

export interface Plan {
  top3: PlanItem[];
  also: PlanItem[];
  blocks: PlanBlock[];
}

export type LoadBand = "ok" | "elevated" | "high";

export interface Load {
  score: number;
  band: LoadBand;
  reasons: string[];
}

export interface Today {
  date: string;
  generated_at: string | null;
  missing: boolean;
  headline: string;
  events: CalEvent[];
  tomorrow: CalEvent[];
  focus_windows: FocusWindow[];
  meeting_min: number;
  top3_proposed: ProposedTask[];
  plan: Plan | null;
  risks: string[];
  // Not guaranteed when the brief is missing.
  load?: Load | null;
  proposals_pending: number;
  paused: boolean;
  // The contract shows an empty list only; items may be strings or objects.
  errors: Array<string | Record<string, unknown>>;
}

export type CiStatus = "passing" | "failing" | "pending" | "none" | "unknown";

export interface ReviewRequest {
  repo: string;
  number: number;
  title: string;
  url: string;
  author: string;
  stale: boolean;
}

export interface MyPr {
  repo: string;
  number: number;
  title: string;
  url: string;
  draft: boolean;
  stale: boolean;
  ci?: CiStatus;
}

export interface Issue {
  repo: string;
  number: number;
  title: string;
  url: string;
  stale: boolean;
}

export interface JiraIssue {
  key: string;
  summary: string;
  status: string;
  status_category: string;
  priority: string;
  url: string;
}

// The server fills these from a proposal payload, so any field may be null.
export interface Diagnosis {
  pr_url: string | null;
  summary: string | null;
  likely_cause: string | null;
  suggested_fix: string | null;
  at: string;
}

export interface Work {
  reviews: ReviewRequest[];
  my_prs: MyPr[];
  issues: Issue[];
  jira: JiraIssue[];
  diagnoses: Diagnosis[];
}

export type ProposalStatus = "pending" | "approved" | "rejected" | "executed" | "failed";

export interface Proposal {
  id: string;
  agent: string;
  action: string;
  target: string;
  preview: string;
  reason: string;
  undo: string;
  level: string;
  safe: boolean;
  status: ProposalStatus;
  created: string;
  decided: string | null;
  executed: string | null;
  result: string | null;
}

export interface ProposalsResponse {
  proposals: Proposal[];
}

export interface ApproveResponse {
  ok: boolean;
  status: "executed" | "failed";
  result: string;
}

export interface ApproveSafeResponse {
  approved: string[];
  results: Record<string, string>;
}

export type AgentName =
  | "brief"
  | "triage-github"
  | "triage-jira"
  | "triage-calendar"
  | "prep"
  | "inbox"
  | "memory"
  | "review";

export type AgentStatus = "ok" | "error" | "never";

export interface Agent {
  name: string; // one of AgentName, kept open for forward compatibility
  label: string;
  schedule: string;
  last_run: string | null;
  status: AgentStatus;
  last_error: string | null;
  runs_7d: number;
  cost_7d: number;
}

export interface AgentsResponse {
  agents: Agent[];
  paused: boolean;
  spend_7d: number;
  spend_today: number;
}

export interface WellbeingDay {
  date: string;
  mood: number | null;
  energy: number | null;
  stress: number | null;
  load: number | null;
  meeting_min: number | null;
  active_min: number | null;
}

export interface Checkin {
  ts: string;
  mood: number | null;
  energy: number | null;
  stress: number | null;
}

export interface Win {
  date: string;
  text: string;
}

export interface Wellbeing {
  today: Load | null;
  days: WellbeingDay[];
  checkins_today: Checkin[];
  wins: Win[];
  experiment: string | null;
}

export interface CheckinBody {
  mood: number;
  energy?: number;
  stress?: number;
  note?: string;
}

export interface CheckinResponse {
  ok: boolean;
  message?: string;
}

export type NoteKind = "note" | "todo" | "idea";

export interface NoteResponse {
  ok: boolean;
  path: string;
}

export interface MemoryResult {
  title: string;
  path: string;
  snippet: string;
}

export interface MemorySearchResponse {
  wiki: boolean;
  results: MemoryResult[];
}

export interface VisualizeResponse {
  url: string;
}

// ----- Setup (first-run onboarding) -----

export type SetupStatus = "ok" | "warn" | "todo" | "fail";

export type SetupGroup = "Profile" | "Accounts" | "Connectors" | "Vaults" | "Phone" | "Automation" | "Extras";

/** Display order from the contract. */
export const SETUP_GROUPS: readonly SetupGroup[] = [
  "Profile",
  "Accounts",
  "Connectors",
  "Vaults",
  "Phone",
  "Automation",
  "Extras",
];

export type SetupActionKind = "run" | "confirm" | "terminal" | "link";

export interface SetupAction {
  id: string;
  label: string;
  kind: SetupActionKind;
  confirm: string | null; // the side effect, shown before a `confirm` action runs
  url: string | null; // set for `link`
}

export interface SetupItem {
  id: string;
  group: string; // one of SetupGroup, kept open for forward compatibility
  label: string;
  status: SetupStatus;
  detail: string;
  action: SetupAction | null;
}

// Fields can be empty before setup has detected or saved them.
export interface SetupProfile {
  name: string | null;
  github_user: string | null;
  jira_site: string | null;
  private_vault: string | null;
  public_vault: string | null;
  crisis_country: string | null;
  timezone: string | null;
}

export interface SetupVault {
  path: string;
  name: string;
}

export interface SetupNtfy {
  topic: string;
  subscribe_url: string;
}

export interface SetupState {
  complete: boolean;
  profile: SetupProfile;
  vaults: SetupVault[];
  ntfy: SetupNtfy | null;
  items: SetupItem[];
}

export type SetupProfileBody = Partial<
  Record<"name" | "crisis_country" | "private_vault" | "public_vault" | "github_user", string>
>;

export interface SetupProfileResponse {
  ok: boolean;
  profile: SetupProfile;
}

/** `ok: false` still comes back with HTTP 200; `detail` says why. */
export interface SetupRunResponse {
  ok: boolean;
  detail: string;
}

export type ChatEvent =
  | { type: "text"; text: string }
  | { type: "tool"; name: string }
  | { type: "done"; session: string; cost?: number }
  | { type: "error"; error: string };

// ---------- Transport ----------

export class ApiError extends Error {
  readonly status: number; // 0 = network failure / timeout
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
  get isNetwork(): boolean {
    return this.status === 0;
  }
  get isAuth(): boolean {
    return this.status === 401 || this.status === 403;
  }
}

export const SERVER_DOWN_HINT = "Can't reach the server. Is it running? Start it with `me serve`.";

interface RequestOpts {
  method?: "GET" | "POST";
  body?: unknown;
  timeoutMs?: number;
  signal?: AbortSignal;
}

async function request<T>(path: string, opts: RequestOpts = {}): Promise<T> {
  const { method = "GET", body, timeoutMs = 20_000, signal } = opts;
  const ctrl = new AbortController();
  const timer = window.setTimeout(() => ctrl.abort(), timeoutMs);
  const onOuterAbort = () => ctrl.abort();
  signal?.addEventListener("abort", onOuterAbort);

  const headers: Record<string, string> = { "X-Me-Token": token, Accept: "application/json" };
  if (method === "POST") headers["Content-Type"] = "application/json";

  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers,
      body: body === undefined ? (method === "POST" ? "{}" : undefined) : JSON.stringify(body),
      signal: ctrl.signal,
      cache: "no-store",
    });
  } catch (e) {
    if (signal?.aborted) throw e;
    const timedOut = ctrl.signal.aborted;
    throw new ApiError(timedOut ? "The server took too long to answer." : SERVER_DOWN_HINT, 0);
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener("abort", onOuterAbort);
  }

  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }

  if (!res.ok) {
    let msg = `Request failed (${res.status})`;
    if (data && typeof data === "object" && "error" in data) {
      const err = (data as { error: unknown }).error;
      if (typeof err === "string" && err) msg = err;
    }
    if (res.status === 401 || res.status === 403) {
      msg = `${msg}. The access token was rejected; reopen the page from \`me serve\`.`;
    } else if (res.status === 502 || res.status === 504) {
      msg = SERVER_DOWN_HINT;
      throw new ApiError(msg, 0);
    }
    throw new ApiError(msg, res.status);
  }
  if (data === null) throw new ApiError("The server sent an unreadable response.", res.status);
  return data as T;
}

const enc = encodeURIComponent;

// ---------- Endpoints ----------

export const api = {
  today: (signal?: AbortSignal) => request<Today>("/api/today", { signal }),

  work: (signal?: AbortSignal) => request<Work>("/api/work", { signal, timeoutMs: 45_000 }),

  proposals: (status: "pending" | "all", signal?: AbortSignal) =>
    request<ProposalsResponse>(`/api/proposals?status=${status}`, { signal }),

  // Execution can take 5–60 s.
  approve: (id: string) =>
    request<ApproveResponse>(`/api/proposals/${enc(id)}/approve`, { method: "POST", timeoutMs: 120_000 }),

  reject: (id: string) => request<{ ok: boolean }>(`/api/proposals/${enc(id)}/reject`, { method: "POST" }),

  approveSafe: () =>
    request<ApproveSafeResponse>("/api/proposals/approve-safe", { method: "POST", timeoutMs: 300_000 }),

  agents: (signal?: AbortSignal) => request<AgentsResponse>("/api/agents", { signal }),

  runAgent: (name: string) => request<{ ok: boolean }>(`/api/agents/${enc(name)}/run`, { method: "POST" }),

  pause: () => request<{ paused: boolean }>("/api/pause", { method: "POST" }),

  resume: () => request<{ paused: boolean }>("/api/resume", { method: "POST" }),

  wellbeing: (signal?: AbortSignal) => request<Wellbeing>("/api/wellbeing", { signal }),

  checkin: (body: CheckinBody) => request<CheckinResponse>("/api/checkin", { method: "POST", body }),

  note: (kind: NoteKind, text: string) => request<NoteResponse>("/api/note", { method: "POST", body: { kind, text } }),

  memorySearch: (q: string, signal?: AbortSignal) =>
    request<MemorySearchResponse>(`/api/memory/search?q=${enc(q)}`, { signal }),

  memoryVisualize: () => request<VisualizeResponse>("/api/memory/visualize", { method: "POST", timeoutMs: 60_000 }),

  /** `deep` also checks which claude.ai connectors are loaded (about 3 s). */
  setup: (deep = false, signal?: AbortSignal) =>
    request<SetupState>(deep ? "/api/setup?deep=1" : "/api/setup", { signal, timeoutMs: deep ? 45_000 : 20_000 }),

  setupProfile: (body: SetupProfileBody) =>
    request<SetupProfileResponse>("/api/setup/profile", { method: "POST", body }),

  // Some actions take up to 30 s.
  setupRun: (actionId: string) =>
    request<SetupRunResponse>(`/api/setup/run/${enc(actionId)}`, { method: "POST", timeoutMs: 90_000 }),

  /** URL for the SSE chat stream. EventSource can't send headers, so the token rides in the query. */
  chatUrl: (q: string, session?: string | null): string => {
    const params = new URLSearchParams({ q, token });
    if (session) params.set("session", session);
    return `/api/chat?${params.toString()}`;
  },
};

export function parseChatEvent(raw: string): ChatEvent | null {
  try {
    const v: unknown = JSON.parse(raw);
    if (!v || typeof v !== "object" || !("type" in v)) return null;
    const o = v as Record<string, unknown>;
    switch (o.type) {
      case "text":
        return typeof o.text === "string" ? { type: "text", text: o.text } : null;
      case "tool":
        return { type: "tool", name: typeof o.name === "string" ? o.name : "tool" };
      case "done":
        return {
          type: "done",
          session: typeof o.session === "string" ? o.session : "",
          cost: typeof o.cost === "number" ? o.cost : undefined,
        };
      case "error":
        return { type: "error", error: typeof o.error === "string" ? o.error : "Something went wrong." };
      default:
        return null;
    }
  } catch {
    return null;
  }
}
