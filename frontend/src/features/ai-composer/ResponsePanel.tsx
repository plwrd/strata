/**
 * The live model request: send, stream, cancel — and the remote confirmation.
 *
 * The Send button's behaviour depends on the policy verdict the selector already
 * fetched: allowed sends straight away, needs-confirmation opens the remote-review
 * dialog first, and denied cannot send at all (the button is disabled with the
 * reason). The confirmation is not cosmetic: Python refuses a
 * `needs_confirmation` layer unless `confirmed_remote` is set, and only this dialog
 * sets it.
 */

import { useState } from "react";
import { bridge, BridgeCallError } from "../../bridge/client";
import { useStore } from "../../state/store";
import { RemoteConfirmDialog } from "./RemoteConfirmDialog";

type SaveState =
  | { kind: "idle" }
  | { kind: "busy" }
  | { kind: "saved"; title: string }
  | { kind: "error"; message: string };

export function ResponsePanel(): JSX.Element {
  const {
    prompt,
    providerId,
    providers,
    policy,
    aiStreaming,
    aiOutput,
    aiError,
    aiSources,
    aiExecutionId,
    conversationId,
    openNote,
    sendToModel,
    cancelAIRequest,
    clearAIOutput,
    newAIThread,
    reloadTree,
  } = useStore();
  const [confirming, setConfirming] = useState(false);
  const [saveState, setSaveState] = useState<SaveState>({ kind: "idle" });

  const saveOutput = async (
    target: "note" | "report" | "append",
  ): Promise<void> => {
    setSaveState({ kind: "busy" });
    try {
      const saved = await bridge.ai.saveOutput({
        execution_id: aiExecutionId ?? "",
        content: aiOutput,
        title: prompt.trim().split("\n")[0]?.slice(0, 80) || "AI answer",
        target,
        note_id: target === "append" ? (openNote?.metadata.id ?? "") : "",
      });
      setSaveState({ kind: "saved", title: saved.title });
      await reloadTree();
    } catch (error) {
      setSaveState({
        kind: "error",
        message:
          error instanceof BridgeCallError || error instanceof Error
            ? error.message
            : "Saving failed.",
      });
    }
  };

  const provider = providers.find((p) => p.provider_id === providerId);
  const canSend =
    Boolean(prompt.trim()) &&
    Boolean(provider?.configured) &&
    policy?.verdict !== "denied" &&
    !aiStreaming;

  const onSend = (): void => {
    if (policy?.verdict === "needs_confirmation") {
      setConfirming(true);
      return;
    }
    void sendToModel(false);
  };

  return (
    <div className="response">
      <div className="response__actions">
        {aiStreaming ? (
          <button
            type="button"
            className="button button--danger"
            onClick={() => void cancelAIRequest()}
          >
            Stop
          </button>
        ) : (
          <button
            type="button"
            className="button button--ai"
            disabled={!canSend}
            title={policy?.verdict === "denied" ? policy.reason : undefined}
            onClick={onSend}
          >
            {provider?.is_local ? "Ask locally" : "Ask (remote)"}
          </button>
        )}

        {aiOutput && !aiStreaming && (
          <button
            type="button"
            className="button button--ghost"
            onClick={clearAIOutput}
          >
            Clear
          </button>
        )}
      </div>

      {aiError && (
        <p className="composer__status composer__status--error" role="alert">
          {aiError}
        </p>
      )}

      {aiSources.length > 0 && (
        <p className="response__sources" aria-label="Notes used as context">
          <span className="label">Used:</span>{" "}
          {aiSources.map((source) => (
            <span
              key={source.object_id}
              className={`tag ${source.is_private ? "tag--private" : ""}`}
            >
              {source.title}
            </span>
          ))}
        </p>
      )}

      {(aiStreaming || aiOutput) && (
        <div
          className="response__output"
          aria-live="polite"
          aria-label="Model response"
        >
          {aiOutput || (aiStreaming ? "…" : "")}
          {aiStreaming && (
            <span className="response__cursor" aria-hidden="true">
              ▊
            </span>
          )}
        </div>
      )}

      {aiOutput && !aiStreaming && (
        <div className="response__save" aria-label="Save this answer">
          <button
            type="button"
            className="button"
            disabled={saveState.kind === "busy"}
            onClick={() => void saveOutput("note")}
          >
            Save as note
          </button>
          <button
            type="button"
            className="button"
            disabled={saveState.kind === "busy"}
            onClick={() => void saveOutput("report")}
          >
            Save as report
          </button>
          {openNote && (
            <button
              type="button"
              className="button"
              disabled={saveState.kind === "busy"}
              onClick={() => void saveOutput("append")}
            >
              Append to “{openNote.metadata.title.slice(0, 24)}”
            </button>
          )}
          {conversationId && (
            <button
              type="button"
              className="button button--ghost"
              onClick={newAIThread}
            >
              New thread
            </button>
          )}
        </div>
      )}

      {saveState.kind === "saved" && (
        <p className="composer__status composer__status--ok" role="status">
          Saved as “{saveState.title}” — marked ai-inferred, undoable from the
          Changes tab.
        </p>
      )}
      {saveState.kind === "error" && (
        <p className="composer__status composer__status--error" role="alert">
          {saveState.message}
        </p>
      )}

      {confirming && policy && (
        <RemoteConfirmDialog
          policy={policy}
          providerName={provider?.display_name ?? providerId}
          onCancel={() => setConfirming(false)}
          onConfirm={() => {
            setConfirming(false);
            void sendToModel(true);
          }}
        />
      )}
    </div>
  );
}
