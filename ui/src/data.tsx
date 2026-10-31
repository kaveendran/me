import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";
import { api, type AgentsResponse, type ProposalsResponse, type SetupState, type Today } from "./api";
import { useResource, type Resource } from "./hooks";

const POLL_MS = 60_000;
// Setup only changes when the user acts, so poll it gently; the Setup screen refreshes it directly.
const SETUP_POLL_MS = 5 * 60_000;

interface AppData {
  today: Resource<Today>;
  pending: Resource<ProposalsResponse>;
  agents: Resource<AgentsResponse>;
  setup: Resource<SetupState>;
  /** Re-fetch the polled resources; call after any mutation. */
  refreshAll: () => Promise<void>;
  pendingCount: number;
  paused: boolean;
  /** Setup items still `todo` or `fail`; 0 once setup is complete. */
  setupOpen: number;
}

const Ctx = createContext<AppData | null>(null);

export function AppDataProvider({ children }: { children: ReactNode }) {
  const today = useResource((s) => api.today(s), POLL_MS);
  const pending = useResource((s) => api.proposals("pending", s), POLL_MS);
  const agents = useResource((s) => api.agents(s), POLL_MS);
  const setup = useResource((s) => api.setup(false, s), SETUP_POLL_MS);

  const tr = today.refresh;
  const pr = pending.refresh;
  const ar = agents.refresh;
  const refreshAll = useCallback(async () => {
    await Promise.all([tr(), pr(), ar()]);
  }, [tr, pr, ar]);

  const pendingCount =
    pending.data?.proposals.filter((p) => p.status === "pending").length ?? today.data?.proposals_pending ?? 0;
  const paused = agents.data?.paused ?? today.data?.paused ?? false;
  const setupOpen =
    setup.data && !setup.data.complete
      ? setup.data.items.filter((i) => i.status === "todo" || i.status === "fail").length
      : 0;

  const value = useMemo<AppData>(
    () => ({ today, pending, agents, setup, refreshAll, pendingCount, paused, setupOpen }),
    [today, pending, agents, setup, refreshAll, pendingCount, paused, setupOpen],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAppData(): AppData {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAppData outside AppDataProvider");
  return v;
}
