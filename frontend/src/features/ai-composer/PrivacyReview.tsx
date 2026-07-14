/**
 * The privacy review.
 *
 * Shown before any decrypted private content leaves the workspace. It is a modal
 * on purpose: this is one of the few moments where an interruption is the correct
 * design. Every private source is listed by name — "2 private notes" is not
 * informed consent, and the user must be able to see exactly which ones.
 *
 * The dialog cannot be bypassed by the frontend: Python refuses an export
 * containing private sources unless `acknowledge_private` is set, and that flag is
 * only ever set from here.
 */

import { useEffect, useRef } from "react";
import type { ContextPlan } from "../../bridge/types";

interface Props {
  plan: ContextPlan;
  onCancel: () => void;
  onConfirm: (kind: "clipboard" | "files") => void;
}

export function PrivacyReview({
  plan,
  onCancel,
  onConfirm,
}: Props): JSX.Element {
  const dialogRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);

  // Focus lands on Cancel, not on Export: the safe option is the default.
  useEffect(() => {
    cancelRef.current?.focus();
  }, []);

  const privateSources = plan.sources.filter((source) => source.is_private);

  return (
    <div
      className="dialog-backdrop"
      role="presentation"
      onKeyDown={(event) => {
        if (event.key === "Escape") onCancel();
      }}
    >
      <div
        ref={dialogRef}
        className="dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="privacy-title"
        aria-describedby="privacy-body"
      >
        <h2 id="privacy-title" className="dialog__title">
          <span className="tag tag--danger">Private content</span> Review before
          exporting
        </h2>

        <div id="privacy-body" className="dialog__body">
          <p>
            This export contains decrypted information from{" "}
            <strong>{plan.private_layer_names.length}</strong> private layer(s):{" "}
            {plan.private_layer_names.join(", ")}.
          </p>

          <p>
            The exported Markdown will <strong>not be encrypted</strong>. Once
            written, it is an ordinary file that anything on this machine can
            read.
          </p>

          <p className="mono dialog__facts">
            sources: {plan.sources.length} &nbsp;·&nbsp; private:{" "}
            {plan.private_source_count} &nbsp;·&nbsp; est. tokens: ~
            {plan.estimated_tokens.toLocaleString()} &nbsp;·&nbsp; data leaving
            this device: no (files are written locally)
          </p>

          <details className="dialog__details" open>
            <summary>
              Private sources included ({privateSources.length})
            </summary>
            <ul>
              {privateSources.map((source) => (
                <li key={source.source_id}>
                  <span className="mono">{source.source_id}</span> —{" "}
                  {source.title}{" "}
                  <span className="tag tag--private">{source.layer_name}</span>
                </li>
              ))}
            </ul>
          </details>
        </div>

        <div className="dialog__actions">
          <button
            ref={cancelRef}
            type="button"
            className="button"
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            type="button"
            className="button button--danger"
            onClick={() => onConfirm("files")}
          >
            Export decrypted Markdown
          </button>
          <button
            type="button"
            className="button button--danger"
            onClick={() => onConfirm("clipboard")}
          >
            Copy decrypted to clipboard
          </button>
        </div>

        <p className="dialog__footnote">
          Encrypted export packages and “remove private sources” arrive with
          private layers in Milestone 3.
        </p>
      </div>
    </div>
  );
}
