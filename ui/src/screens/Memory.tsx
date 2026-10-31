import { useEffect, useRef, useState } from "react";
import { api, type MemorySearchResponse } from "../api";
import { IconMemory, IconSearch } from "../components/Icons";
import { Card, Empty, ErrorState, Loading, PageHead, Spinner } from "../components/ui";
import { useToast } from "../toast";

export function MemoryScreen() {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<MemorySearchResponse | null>(null);
  const [searched, setSearched] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [viz, setViz] = useState<"idle" | "busy">("idle");
  const [vizUrl, setVizUrl] = useState<string | null>(null);
  const ctrl = useRef<AbortController | null>(null);
  const toast = useToast();

  const search = async (term: string) => {
    const t = term.trim();
    ctrl.current?.abort();
    if (t.length < 2) {
      setRes(null);
      setSearched("");
      setError(null);
      setLoading(false);
      return;
    }
    const c = new AbortController();
    ctrl.current = c;
    setLoading(true);
    setError(null);
    try {
      const r = await api.memorySearch(t, c.signal);
      if (!c.signal.aborted) {
        setRes(r);
        setSearched(t);
      }
    } catch (e) {
      if (!c.signal.aborted) setError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      if (!c.signal.aborted) setLoading(false);
    }
  };

  // Debounced search as you type.
  useEffect(() => {
    const id = window.setTimeout(() => void search(q), 300);
    return () => window.clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  useEffect(() => () => ctrl.current?.abort(), []);

  // An empty query tells us cheaply whether the wiki exists yet.
  const [wikiReady, setWikiReady] = useState<boolean | null>(null);
  useEffect(() => {
    const c = new AbortController();
    api
      .memorySearch("", c.signal)
      .then((r) => setWikiReady(r.wiki))
      .catch(() => undefined);
    return () => c.abort();
  }, []);

  const openGraph = async () => {
    // Open the tab synchronously so popup blockers allow it, then point it at the URL.
    const win = window.open("about:blank", "_blank");
    setViz("busy");
    try {
      const r = await api.memoryVisualize();
      setVizUrl(r.url);
      if (win && !win.closed) {
        win.opener = null;
        win.location.href = r.url;
      } else {
        toast.show("Graph is ready. Use the link below to open it.", "info");
      }
    } catch (e) {
      win?.close();
      toast.show(e instanceof Error ? e.message : "Couldn't start the memory graph.", "error");
    } finally {
      setViz("idle");
    }
  };

  const wikiMissing = res ? res.wiki === false : wikiReady === false;

  return (
    <>
      <PageHead
        title="Memory"
        sub="Search people, projects and decisions from your private vault."
        actions={
          <button type="button" className="btn" onClick={() => void openGraph()} disabled={viz === "busy"}>
            {viz === "busy" ? <Spinner size={14} /> : <IconMemory size={16} />}
            Open memory graph
          </button>
        }
      />
      {vizUrl ? (
        <p className="small muted">
          Memory graph:{" "}
          <a href={vizUrl} target="_blank" rel="noopener noreferrer">
            {vizUrl}
          </a>
        </p>
      ) : null}
      <form
        className="search-box"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          void search(q);
        }}
      >
        <IconSearch size={18} />
        <label htmlFor="mem-q" className="sr-only">
          Search memory
        </label>
        <input
          id="mem-q"
          type="search"
          placeholder="Search memory… e.g. a name, a project, a decision"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          autoComplete="off"
        />
        {loading ? <Spinner size={16} label="Searching" /> : null}
      </form>

      {error ? <ErrorState compact error={error} onRetry={() => void search(q)} /> : null}

      {wikiMissing ? (
        <Card>
          <Empty icon={<IconMemory size={20} />}>
            <strong>Memory isn't built yet.</strong>
            <div className="muted">
              It's generated nightly from your private vault. Check back tomorrow morning, or run the memory agent from the
              Agents screen.
            </div>
          </Empty>
        </Card>
      ) : loading && !res ? (
        <Card>
          <Loading rows={3} />
        </Card>
      ) : res && searched ? (
        res.results.length ? (
          <ul className="mem-results" aria-label={`Results for ${searched}`}>
            {res.results.map((r, i) => (
              <li key={`${r.path}-${i}`} className="card mem-result">
                <div className="mem-title">{r.title}</div>
                <div className="mem-path mono">{r.path}</div>
                {r.snippet ? <p className="mem-snippet">{r.snippet}</p> : null}
              </li>
            ))}
          </ul>
        ) : (
          <Card>
            <Empty>No matches for "{searched}". Try a shorter or different word.</Empty>
          </Card>
        )
      ) : !error ? (
        <Card>
          <Empty icon={<IconSearch size={20} />}>Type at least two characters to search.</Empty>
        </Card>
      ) : null}
    </>
  );
}
