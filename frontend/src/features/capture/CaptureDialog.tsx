/**
 * Quick capture: the fastest path from "I found something" to "it's in Strata".
 *
 * Everything lands in the Inbox as a raw capture — organising is deliberately
 * someone else's job (the processing pipeline's, later). URL import calls the
 * SSRF-guarded backend fetch; the page's text is stored as untrusted data.
 */

import { useState } from "react";
import { bridge, BridgeCallError } from "../../bridge/client";
import { useStore } from "../../state/store";

type Mode = "text" | "url";
type Status =
  { kind: "idle" } | { kind: "busy" } | { kind: "error"; message: string };

export function CaptureDialog(props: { onClose: () => void }): JSX.Element {
  const { layers, reloadTree } = useStore();
  const [mode, setMode] = useState<Mode>("text");
  const [content, setContent] = useState("");
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [reason, setReason] = useState("");
  const [layerId, setLayerId] = useState("");
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  const writableLayers = layers.filter(
    (layer) => layer.state === "mounted" || layer.state === "unlocked",
  );
  const canSubmit =
    status.kind !== "busy" &&
    (mode === "text" ? content.trim().length > 0 : url.trim().length > 0);

  const submit = async (): Promise<void> => {
    setStatus({ kind: "busy" });
    try {
      if (mode === "text") {
        await bridge.notes.capture({
          content,
          title,
          layer_id: layerId,
          capture_reason: reason,
        });
      } else {
        await bridge.notes.importUrl({
          url,
          layer_id: layerId,
          capture_reason: reason,
        });
      }
      await reloadTree();
      props.onClose();
    } catch (error) {
      setStatus({
        kind: "error",
        message:
          error instanceof BridgeCallError || error instanceof Error
            ? error.message
            : "The capture failed.",
      });
    }
  };

  return (
    <div className="dialog-backdrop" role="presentation">
      <div
        className="dialog capture-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Capture"
      >
        <header className="dialog__header">
          <h2 className="dialog__title">Capture</h2>
          <div className="segmented" role="group" aria-label="Capture kind">
            <button
              type="button"
              className={`segmented__option ${mode === "text" ? "segmented__option--active" : ""}`}
              aria-pressed={mode === "text"}
              onClick={() => setMode("text")}
            >
              Text
            </button>
            <button
              type="button"
              className={`segmented__option ${mode === "url" ? "segmented__option--active" : ""}`}
              aria-pressed={mode === "url"}
              onClick={() => setMode("url")}
            >
              From URL
            </button>
          </div>
        </header>

        {mode === "text" ? (
          <>
            <label className="composer__field">
              <span className="label">Title (optional)</span>
              <input
                className="input"
                value={title}
                placeholder="Derived from the first line if empty"
                onChange={(event) => setTitle(event.target.value)}
              />
            </label>
            <label className="composer__field">
              <span className="label">Content</span>
              <textarea
                className="textarea capture-dialog__content"
                value={content}
                placeholder="Paste or type anything. It lands raw in the Inbox — organise later."
                aria-label="Capture content"
                onChange={(event) => setContent(event.target.value)}
              />
            </label>
          </>
        ) : (
          <label className="composer__field">
            <span className="label">Page URL</span>
            <input
              className="input"
              value={url}
              placeholder="https://…"
              inputMode="url"
              aria-label="Page URL"
              onChange={(event) => setUrl(event.target.value)}
            />
            <span className="capture-dialog__hint">
              The page's text is fetched once and stored as an untrusted
              capture. Private and local addresses are refused.
            </span>
          </label>
        )}

        <label className="composer__field">
          <span className="label">Why keep this? (optional)</span>
          <input
            className="input"
            value={reason}
            placeholder="e.g. relevant to the launch decision"
            onChange={(event) => setReason(event.target.value)}
          />
        </label>

        {writableLayers.length > 1 && (
          <label className="composer__field">
            <span className="label">Layer</span>
            <select
              className="select"
              value={layerId}
              aria-label="Capture layer"
              onChange={(event) => setLayerId(event.target.value)}
            >
              <option value="">First public layer</option>
              {writableLayers.map((layer) => (
                <option key={layer.id} value={layer.id}>
                  {layer.display_name}
                  {layer.visibility === "private" ? " (private)" : ""}
                </option>
              ))}
            </select>
          </label>
        )}

        {status.kind === "error" && (
          <p className="composer__status composer__status--error" role="alert">
            {status.message}
          </p>
        )}

        <div className="dialog__actions">
          <button
            type="button"
            className="button button--primary"
            disabled={!canSubmit}
            onClick={() => void submit()}
          >
            {status.kind === "busy"
              ? "Capturing…"
              : mode === "url"
                ? "Import page"
                : "Capture"}
          </button>
          <button
            type="button"
            className="button button--ghost"
            onClick={props.onClose}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
