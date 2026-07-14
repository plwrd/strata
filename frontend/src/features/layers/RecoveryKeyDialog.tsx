/**
 * The recovery key, shown once.
 *
 * This dialog is intentionally hard to dismiss. There is no second copy anywhere —
 * not on disk, not in the header, not in the store — so a user who clicks past it
 * has permanently given up their only fallback. The confirm button stays disabled
 * until they tick the box that says they wrote it down.
 */

import { useState } from "react";
import { stubbornClipboardWarning } from "./clipboardNotice";

interface Props {
  layerName: string;
  recoveryKey: string;
  onClose: () => void;
}

export function RecoveryKeyDialog({
  layerName,
  recoveryKey,
  onClose,
}: Props): JSX.Element {
  const [acknowledged, setAcknowledged] = useState(false);
  const [copied, setCopied] = useState(false);

  const copy = async (): Promise<void> => {
    await navigator.clipboard.writeText(recoveryKey);
    setCopied(true);
  };

  const download = (): void => {
    // A local file the user controls. Blob URLs are permitted by the CSP
    // (`img-src`/`media-src` blob:), and this never leaves the machine.
    const blob = new Blob(
      [
        `Strata recovery key\n`,
        `Layer: ${layerName}\n`,
        `Created: ${new Date().toISOString()}\n\n`,
        `${recoveryKey}\n\n`,
        `This key opens the layer without the password.\n`,
        `Anyone who has it can read the layer. Store it somewhere safe and offline.\n`,
        `Strata does not keep a copy. If you lose both this key and the password,\n`,
        `the contents of the layer are unrecoverable.\n`,
      ],
      { type: "text/plain" },
    );
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `strata-recovery-key-${layerName.replace(/\W+/g, "-").toLowerCase()}.txt`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="dialog-backdrop" role="presentation">
      <div
        className="dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="recovery-title"
        aria-describedby="recovery-body"
      >
        <h2 id="recovery-title" className="dialog__title">
          <span className="tag tag--warning">Shown once</span> Recovery key for{" "}
          {layerName}
        </h2>

        <div id="recovery-body" className="dialog__body">
          <p>
            Write this down and keep it somewhere safe and offline. It opens the
            layer
            <strong> without the password</strong>.
          </p>

          <pre className="recovery-key" data-testid="recovery-key">
            {recoveryKey}
          </pre>

          <p className="dialog__warning">
            <span className="tag tag--danger">There is no second copy</span>{" "}
            Strata does not store this key and cannot show it to you again. If
            you lose it and forget the password, the layer&apos;s contents are
            gone permanently.
          </p>

          <div className="dialog__actions dialog__actions--inline">
            <button
              type="button"
              className="button"
              onClick={() => void copy()}
            >
              {copied ? "Copied" : "Copy"}
            </button>
            <button type="button" className="button" onClick={download}>
              Save to a file
            </button>
          </div>

          {copied && (
            <p className="dialog__footnote">{stubbornClipboardWarning}</p>
          )}

          <label className="dialog__choice dialog__choice--confirm">
            <input
              type="checkbox"
              checked={acknowledged}
              onChange={(event) => setAcknowledged(event.target.checked)}
            />
            <span>I have saved this recovery key somewhere safe.</span>
          </label>
        </div>

        <div className="dialog__actions">
          <button
            type="button"
            className="button button--primary"
            disabled={!acknowledged}
            onClick={onClose}
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
