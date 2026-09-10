/**
 * Browser research: search, scrape, analyse, file.
 *
 * The browser itself is not in this file. It is either the pane beside this one
 * (a native Qt view — React cannot draw it, only ask for it) or a real Chrome
 * driven over a loopback port. This panel is the control surface for whichever
 * is configured, and the two behave identically from here.
 *
 * Three deliberate seams in the flow:
 *
 * - **Scrape is not capture.** You see the text before anything is written.
 * - **Layers are ticked, not inferred.** The ticked layers bound retrieval *and*
 *   every operation the plan may contain; nothing outside them can be touched.
 * - **Filing proposes, it does not apply.** The plan is handed to the Changes
 *   panel and goes through the same review → approve → apply flow as every
 *   other AI change.
 *
 * Reading is asynchronous: extraction runs inside the page, so `scrapeTab` and
 * `captureTab` return a request id and the answer arrives on `onPage`. The
 * panel waits on the id it is expecting and ignores every other event.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { bridge, BridgeCallError } from "../../bridge/client";
import type {
  BlurStreamEvent,
  BrowserStatus,
  BrowserTab,
  DigestMode,
  PageStreamEvent,
  ScrapedPage,
} from "../../bridge/types";
import { useStore } from "../../state/store";

type Busy =
  | "idle"
  | "launching"
  | "searching"
  | "listing"
  | "scraping"
  | "capturing"
  | "filing";

const PREVIEW_CHARS = 1200;

const ENGINE_LABELS: Record<string, string> = {
  duckduckgo: "DuckDuckGo",
  google: "Google",
  bing: "Bing",
  brave: "Brave",
  kagi: "Kagi",
  startpage: "Startpage",
};

export function BrowserPanel(): JSX.Element {
  const state = useStore();
  const [status, setStatus] = useState<BrowserStatus | null>(null);
  const [blur, setBlur] = useState(false);
  const [mobile, setMobile] = useState(false);
  const [engines, setEngines] = useState<string[]>([]);
  const [engine, setEngine] = useState("");
  const [query, setQuery] = useState("");
  const [tabs, setTabs] = useState<BrowserTab[]>([]);
  const [targetId, setTargetId] = useState("");
  const [page, setPage] = useState<ScrapedPage | null>(null);
  const [captureNoteId, setCaptureNoteId] = useState("");
  const [reason, setReason] = useState("");
  const [mode, setMode] = useState<DigestMode>("full");
  const [instruction, setInstruction] = useState("");
  const [tagsText, setTagsText] = useState("");
  const [scopeIds, setScopeIds] = useState<string[]>([]);
  const [fileLayerId, setFileLayerId] = useState("");
  const [busy, setBusy] = useState<Busy>("idle");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  // The read we are waiting on, and what to do when it lands. A ref, not state,
  // so the signal handler reads the latest value without re-subscribing.
  const pendingRead = useRef<{
    requestId: string;
    resolve: (page: ScrapedPage, noteId: string) => void;
    reject: (message: string) => void;
  } | null>(null);

  // Only layers that are open can be read or written; a locked private layer is
  // not an option, and saying so is better than failing at apply time.
  const usableLayers = useMemo(
    () =>
      state.layers.filter(
        (layer) => layer.state === "mounted" || layer.state === "unlocked",
      ),
    [state.layers],
  );

  useEffect(() => {
    void refreshStatus();
    let drop: (() => void) | null = null;
    let cancelled = false;
    void bridge.browser
      .onPage((raw) => {
        const event = JSON.parse(raw) as PageStreamEvent;
        const pending = pendingRead.current;
        if (!pending || event.requestId !== pending.requestId) return;
        pendingRead.current = null;
        if (event.kind === "error" || !event.page) {
          pending.reject(event.error ?? "The page could not be read.");
          return;
        }
        pending.resolve(event.page, event.note?.metadata.id ?? "");
      })
      .then((unsubscribe) => {
        if (cancelled) unsubscribe();
        else drop = unsubscribe;
      });
    let dropBlur: (() => void) | null = null;
    void bridge.browser
      .onBlur((raw) => {
        const event = JSON.parse(raw) as BlurStreamEvent;
        setBlur(event.enabled);
      })
      .then((unsubscribe) => {
        if (cancelled) unsubscribe();
        else dropBlur = unsubscribe;
      });
    return () => {
      cancelled = true;
      drop?.();
      dropBlur?.();
    };
    // Once per mount: the panel asks the host what it can do, and listens for
    // the reads it starts and for blur changes it did not make.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The pane can also be toggled from the keyboard, where this panel is not
  // the one doing it — re-read rather than show a status we know is stale.
  useEffect(() => {
    if (state.browserRevision === 0) return;
    void refreshStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.browserRevision]);

  // Default the scope to every usable layer the first time they load. After
  // that it is the user's, and it is never silently widened.
  useEffect(() => {
    setScopeIds((current) =>
      current.length ? current : usableLayers.map((layer) => layer.id),
    );
  }, [usableLayers]);

  const run = async (kind: Busy, work: () => Promise<void>): Promise<void> => {
    setBusy(kind);
    setError("");
    try {
      await work();
    } catch (caught) {
      setError(describe(caught));
    } finally {
      setBusy("idle");
    }
  };

  const refreshStatus = async (): Promise<void> => {
    try {
      const result = await bridge.browser.getStatus();
      setStatus(result.status);
      setBlur(result.status.blur_enabled);
      setMobile(result.status.mobile_mode);
      setEngines(result.engines);
      if (result.status.running) await refreshTabs();
    } catch (caught) {
      setError(describe(caught));
    }
  };

  const toggleBlur = (): void => {
    // Optimistic: the button flips at once; `onBlur` confirms, and also catches
    // the application hotkey, which never comes through this handler.
    const next = !blur;
    setBlur(next);
    void bridge.browser
      .setBlur(next)
      .catch((caught) => setError(describe(caught)));
  };

  const toggleMobile = (): void => {
    const next = !mobile;
    setMobile(next);
    void bridge.browser
      .setMobile(next)
      .catch((caught) => setError(describe(caught)));
  };

  const refreshTabs = async (): Promise<void> => {
    const { tabs: open } = await bridge.browser.listTabs();
    setTabs(open);
    setTargetId((current) =>
      open.some((tab) => tab.target_id === current)
        ? current
        : (open[0]?.target_id ?? ""),
    );
  };

  /** Start a read and wait for the event carrying its request id. */
  const awaitRead = (
    start: () => Promise<{ request_id: string }>,
  ): Promise<{ page: ScrapedPage; noteId: string }> =>
    new Promise((resolve, reject) => {
      void start()
        .then(({ request_id }) => {
          pendingRead.current = {
            requestId: request_id,
            resolve: (readPage, noteId) => resolve({ page: readPage, noteId }),
            reject: (message) => reject(new Error(message)),
          };
        })
        .catch(reject);
    });

  const launch = (): Promise<void> =>
    run("launching", async () => {
      const { status: next } = await bridge.browser.launch();
      setStatus(next);
      await refreshTabs();
    });

  const closeBrowser = (): Promise<void> =>
    run("launching", async () => {
      const { status: next } = await bridge.browser.closeBrowser();
      setStatus(next);
      setTabs([]);
    });

  const search = (): Promise<void> =>
    run("searching", async () => {
      if (!query.trim()) return;
      const { tab } = await bridge.browser.search(query, engine);
      setTargetId(tab.target_id);
      setPage(null);
      setCaptureNoteId("");
      await refreshStatus();
    });

  const scrape = (): Promise<void> =>
    run("scraping", async () => {
      const result = await awaitRead(() => bridge.browser.scrapeTab(targetId));
      setPage(result.page);
      setCaptureNoteId("");
      setNotice("");
    });

  const captureRequest = () => ({
    target_id: targetId,
    layer_id: fileLayerId,
    capture_reason: reason,
    tags: tagsText
      .split(",")
      .map((tag) => tag.trim())
      .filter(Boolean),
    mode,
    instruction,
    provider_id: state.providerId,
    model: state.model || "default",
    confirmed_remote: false,
  });

  const capture = (): Promise<void> =>
    run("capturing", async () => {
      const result = await awaitRead(() =>
        bridge.browser.captureTab(captureRequest()),
      );
      setPage(result.page);
      setCaptureNoteId(result.noteId);
      setNotice(
        mode === "full"
          ? `Captured “${result.page.title}” into the Inbox.`
          : `Saved a ${mode === "outline" ? "key-points" : "brief"} digest into the Inbox.`,
      );
      await state.reloadTree();
    });

  // Capture (if it has not happened yet) and ask the model where it belongs.
  // The plan itself is reviewed in Changes — this panel never applies anything.
  const analyseAndFile = (): Promise<void> =>
    run("filing", async () => {
      let noteId = captureNoteId;
      if (!noteId) {
        const result = await awaitRead(() =>
          bridge.browser.captureTab(captureRequest()),
        );
        setPage(result.page);
        noteId = result.noteId;
        setCaptureNoteId(noteId);
        await state.reloadTree();
      }
      const { request_id } = await bridge.operations.fileResearch({
        provider_id: state.providerId,
        model: state.model || "default",
        note_ids: [noteId],
        layer_ids: scopeIds,
        target_layer_id: fileLayerId,
        confirmed_remote: false,
      });
      state.handOffPlanRequest(request_id, scopeIds);
      setNotice("Analysing — the proposal will open in Changes for review.");
    });

  const toggleScope = (layerId: string): void =>
    setScopeIds((current) =>
      current.includes(layerId)
        ? current.filter((id) => id !== layerId)
        : [...current, layerId],
    );

  const working = busy !== "idle";
  const running = Boolean(status?.running);
  const embedded = status?.backend !== "chrome";
  const canFile =
    running && scopeIds.length > 0 && (Boolean(page) || Boolean(targetId));

  if (status && !status.enabled) {
    return (
      <section className="research" aria-label="Browser research">
        <h2 className="sidebar__heading">Research</h2>
        <p className="empty-state">
          Browser research is off. Turning it on opens a browser pane beside
          this one, so research can reach pages a plain fetch cannot — the ones
          behind a login, and the ones that are blank until JavaScript runs.
          Enable it in Settings.
        </p>
      </section>
    );
  }

  return (
    <section className="research" aria-label="Browser research">
      <h2 className="sidebar__heading">Research</h2>

      <p className="research__status mono" role="status">
        {status?.detail ?? "Checking the browser…"}
      </p>

      {running && embedded && (
        <p className="research__hint">
          The built-in browser plays WebM/AV1 video but not H.264 — the format
          x.com, YouTube and most sites use — so their videos stay blank. For
          video, switch the browser to <strong>Your own Chrome</strong> in
          Settings. Images, text and scraping work here regardless.
        </p>
      )}

      <div className="research__actions">
        {!running ? (
          <button
            type="button"
            className="button button--primary"
            disabled={working}
            onClick={() => void launch()}
          >
            {busy === "launching"
              ? "Opening…"
              : embedded
                ? "Open browser pane"
                : "Open browser"}
          </button>
        ) : (
          <button
            type="button"
            className="button button--ghost"
            disabled={working}
            onClick={() => void closeBrowser()}
          >
            {embedded ? "Close pane" : "Close browser"}
          </button>
        )}
        {running && status?.blur_supported && (
          <button
            type="button"
            className={`button ${blur ? "button--primary" : ""}`}
            aria-pressed={blur}
            title="Blur images, video and canvas (Ctrl/Cmd+Shift+X)"
            onClick={toggleBlur}
          >
            {blur ? "Media blurred" : "Blur media"}
          </button>
        )}
        {running && embedded && (
          <button
            type="button"
            className={`button ${mobile ? "button--primary" : ""}`}
            aria-pressed={mobile}
            title="Serve sites their mobile layout"
            onClick={toggleMobile}
          >
            {mobile ? "Mobile site" : "Desktop site"}
          </button>
        )}
      </div>
      {running && status?.blur_supported && (
        <p className="research__hint">
          Blur hides images, video and canvas so the page is safe to have on a
          shared screen; text stays readable. Toggle it anywhere with{" "}
          <kbd>Ctrl/Cmd+Shift+X</kbd>, and set the strength in Settings.
        </p>
      )}

      <div className="research__search">
        <label className="composer__field">
          <span className="label">Search the web</span>
          <input
            className="input"
            type="search"
            value={query}
            placeholder="What are you looking for?"
            aria-label="Search the web"
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void search();
            }}
          />
        </label>
        <label className="composer__field">
          <span className="label">Engine</span>
          <select
            className="select"
            value={engine}
            aria-label="Search engine"
            onChange={(event) => setEngine(event.target.value)}
          >
            <option value="">Default</option>
            {engines.map((name) => (
              <option key={name} value={name}>
                {ENGINE_LABELS[name] ?? name}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="button"
          disabled={working || !query.trim()}
          onClick={() => void search()}
        >
          {busy === "searching" ? "Opening…" : "Search"}
        </button>
      </div>

      {/* The pane shows one page at a time and has its own address bar, so a
          tab list would be a second, lying copy of it. Chrome has real tabs. */}
      {running && !embedded && (
        <div className="research__tabs">
          <div className="research__tabs-header">
            <span className="label">Open tabs</span>
            <button
              type="button"
              className="button button--ghost"
              disabled={working}
              onClick={() => void run("listing", refreshTabs)}
            >
              Refresh
            </button>
          </div>
          {tabs.length === 0 ? (
            <p className="empty-state">No page is open yet.</p>
          ) : (
            <select
              className="select"
              value={targetId}
              aria-label="Tab to read"
              onChange={(event) => {
                setTargetId(event.target.value);
                setPage(null);
                setCaptureNoteId("");
              }}
            >
              {tabs.map((tab) => (
                <option key={tab.target_id} value={tab.target_id}>
                  {(tab.title || tab.url).slice(0, 80)}
                </option>
              ))}
            </select>
          )}
        </div>
      )}

      {/* Capture mode: how much of the page to keep. Brief and Key points run a
          model over the page and save only the digest — the page is discarded. */}
      <label className="composer__field">
        <span className="label">Capture as</span>
        <select
          className="select"
          value={mode}
          aria-label="Capture mode"
          onChange={(event) => setMode(event.target.value as DigestMode)}
        >
          <option value="full">Full text — keep the whole page</option>
          <option value="brief">Brief — AI summary + key points</option>
          <option value="outline">Key points — AI structured extract</option>
        </select>
      </label>
      {mode !== "full" && (
        <label className="composer__field">
          <span className="label">Focus (optional)</span>
          <input
            className="input"
            value={instruction}
            placeholder="e.g. pricing and limits, or the API endpoints"
            aria-label="Digest focus"
            onChange={(event) => setInstruction(event.target.value)}
          />
        </label>
      )}
      <label className="composer__field">
        <span className="label">Tags (optional, comma-separated)</span>
        <input
          className="input"
          value={tagsText}
          placeholder="e.g. vector-search, benchmarks"
          aria-label="Capture tags"
          onChange={(event) => setTagsText(event.target.value)}
        />
      </label>

      <div className="research__actions">
        <button
          type="button"
          className="button"
          disabled={working || !running}
          onClick={() => void scrape()}
        >
          {busy === "scraping" ? "Reading…" : "Scrape page"}
        </button>
        <button
          type="button"
          className="button"
          disabled={working || !running}
          onClick={() => void capture()}
        >
          {busy === "capturing"
            ? mode === "full"
              ? "Capturing…"
              : "Digesting…"
            : mode === "full"
              ? "Capture only"
              : "Digest & capture"}
        </button>
      </div>
      {mode !== "full" && (
        <p className="research__hint">
          The page is run through your AI model and only the digest is kept —
          the full text is never saved. Uses{" "}
          {state.model || "the default model"}.
        </p>
      )}

      {page && (
        <div className="research__preview">
          <p className="research__preview-title">{page.title}</p>
          <p className="research__preview-url mono">{page.url}</p>
          {/* Deliberately a <pre>: page text is data, and rendering it as
              Markdown would hand a scraped page a say in the UI. */}
          <pre className="research__preview-text">
            {page.text.slice(0, PREVIEW_CHARS)}
            {page.text.length > PREVIEW_CHARS ? "\n…" : ""}
          </pre>
          <p className="research__preview-meta mono">
            {page.char_count.toLocaleString()} characters
            {page.truncated ? " (truncated)" : ""}
          </p>
        </div>
      )}

      <fieldset className="research__scope">
        <legend className="label">Layers to search and file into</legend>
        {usableLayers.length === 0 ? (
          <p className="empty-state">No layer is open.</p>
        ) : (
          usableLayers.map((layer) => (
            <label key={layer.id} className="research__scope-option">
              <input
                type="checkbox"
                checked={scopeIds.includes(layer.id)}
                onChange={() => toggleScope(layer.id)}
              />
              <span>
                {layer.display_name}
                {layer.visibility === "private" ? " (private)" : ""}
              </span>
            </label>
          ))
        )}
      </fieldset>

      <label className="composer__field">
        <span className="label">New nodes land in</span>
        <select
          className="select"
          value={fileLayerId}
          aria-label="Layer for new nodes"
          onChange={(event) => setFileLayerId(event.target.value)}
        >
          <option value="">First public layer</option>
          {usableLayers
            .filter((layer) => scopeIds.includes(layer.id))
            .map((layer) => (
              <option key={layer.id} value={layer.id}>
                {layer.display_name}
                {layer.visibility === "private" ? " (private)" : ""}
              </option>
            ))}
        </select>
      </label>

      <label className="composer__field">
        <span className="label">Why keep this? (optional)</span>
        <input
          className="input"
          value={reason}
          placeholder="e.g. background for the routing decision"
          onChange={(event) => setReason(event.target.value)}
        />
      </label>

      <button
        type="button"
        className="button button--primary"
        disabled={working || !canFile}
        onClick={() => void analyseAndFile()}
      >
        {busy === "filing" ? "Analysing…" : "Analyse & file"}
      </button>
      <p className="research__hint">
        Finds the nodes this page belongs to in the ticked layers, then proposes
        subnodes and added context. Nothing is written until you approve it in
        Changes.
      </p>

      {notice && (
        <p className="composer__status" role="status">
          {notice}
        </p>
      )}
      {error && (
        <p className="composer__status composer__status--error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

function describe(error: unknown): string {
  if (error instanceof BridgeCallError || error instanceof Error) {
    return error.message;
  }
  return "Something went wrong in the browser.";
}
