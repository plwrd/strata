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
import { useStore } from "../../state/store";
import { RemoteConfirmDialog } from "./RemoteConfirmDialog";

export function ResponsePanel(): JSX.Element {
  const {
    prompt,
    providerId,
    providers,
    policy,
    aiStreaming,
    aiOutput,
    aiError,
    sendToModel,
    cancelAIRequest,
    clearAIOutput,
  } = useStore();
  const [confirming, setConfirming] = useState(false);

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
