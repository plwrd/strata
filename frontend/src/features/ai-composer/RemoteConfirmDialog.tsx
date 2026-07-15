/**
 * Confirm before content leaves the machine.
 *
 * Shown when a layer's policy is "remote AI with confirmation". It states plainly
 * what is about to happen: which provider, how many objects, how many of them
 * private, and that the data is leaving this device. Focus lands on Cancel — the
 * safe choice is the default.
 */

import { useEffect, useRef } from "react";
import type { PolicyView } from "../../bridge/types";

interface Props {
  policy: PolicyView;
  providerName: string;
  onCancel: () => void;
  onConfirm: () => void;
}

export function RemoteConfirmDialog({
  policy,
  providerName,
  onCancel,
  onConfirm,
}: Props): JSX.Element {
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => cancelRef.current?.focus(), []);

  return (
    <div
      className="dialog-backdrop"
      role="presentation"
      onKeyDown={(event) => {
        if (event.key === "Escape") onCancel();
      }}
    >
      <div
        className="dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="remote-title"
        aria-describedby="remote-body"
      >
        <h2 id="remote-title" className="dialog__title">
          <span className="tag tag--warning">Leaving this device</span> Send to{" "}
          {providerName}?
        </h2>

        <div id="remote-body" className="dialog__body">
          <p>{policy.reason}</p>

          <p className="mono dialog__facts">
            provider: {providerName} &nbsp;·&nbsp; objects:{" "}
            {policy.object_count} &nbsp;·&nbsp; private:{" "}
            {policy.private_object_count} &nbsp;·&nbsp; destination: this
            content leaves your machine
          </p>

          {policy.private_object_count > 0 && (
            <p className="dialog__warning">
              <span className="tag tag--danger">Private content</span>{" "}
              {policy.private_object_count} of the objects come from private
              layers. Their decrypted content will be sent.
            </p>
          )}
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
            onClick={onConfirm}
          >
            Send to {providerName}
          </button>
        </div>
      </div>
    </div>
  );
}
