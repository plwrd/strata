/**
 * The recovery key, shown once — and only if you ask for it.
 *
 * A recovery key is a second password: it opens the layer without the real one.
 * There is no second copy anywhere — not on disk, not in the header, not in the
 * store — so if it is lost along with the password the layer is gone. But it is
 * optional: a password alone is enough, so the key is hidden behind a Show
 * button (no shoulder-surfing) and the dialog can be dismissed without saving
 * it. Reveal, copy or download it only if you want that fallback.
 */

import { useState } from "react";
import { DialogPortal } from "../../ui/DialogPortal";
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
  const [copied, setCopied] = useState(false);
  // The key is hidden by default: a recovery key is a second password, and
  // showing it unprompted is a shoulder-surfing risk for someone who only means
  // to use the layer's password. Revealed on demand.
  const [revealed, setRevealed] = useState(false);

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
    <DialogPortal>
      <div className="dialog-backdrop" role="presentation">
        <div
          className="dialog"
          role="alertdialog"
          aria-modal="true"
          aria-labelledby="recovery-title"
          aria-describedby="recovery-body"
        >
          <h2 id="recovery-title" className="dialog__title">
            <span className="tag tag--warning">Shown once</span> Recovery key
            for {layerName}
          </h2>

          <div id="recovery-body" className="dialog__body">
            <p>
              A recovery key opens the layer
              <strong> without the password</strong>. It is optional — if your
              password is enough for you, you can skip this. Kept hidden so no
              one nearby sees it; reveal it only when you are ready to save it.
            </p>

            {revealed ? (
              <pre className="recovery-key" data-testid="recovery-key">
                {recoveryKey}
              </pre>
            ) : (
              <pre
                className="recovery-key recovery-key--hidden"
                aria-hidden="true"
                data-testid="recovery-key-hidden"
              >
                {"•".repeat(24)}
              </pre>
            )}

            <div className="dialog__actions dialog__actions--inline">
              <button
                type="button"
                className="button"
                aria-pressed={revealed}
                onClick={() => setRevealed((value) => !value)}
              >
                {revealed ? "Hide" : "Show"}
              </button>
            </div>

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
          </div>

          <div className="dialog__actions">
            <button
              type="button"
              className="button button--primary"
              onClick={onClose}
            >
              Done
            </button>
          </div>
        </div>
      </div>
    </DialogPortal>
  );
}
