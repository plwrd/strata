/**
 * Unlock a private layer.
 *
 * The failure message is deliberately vague ("could not be unlocked") and identical
 * for a wrong password, a corrupt header, and an empty layer — Python returns one
 * generic error, and this dialog does not embellish it. A helpful "wrong password"
 * would also be helpful to someone who is not the owner.
 *
 * The password is held in component state for the length of one keystroke-to-submit
 * cycle and then cleared. It never reaches the store.
 */

import { useState } from "react";
import { BridgeCallError } from "../../bridge/client";
import type { LayerDescriptor } from "../../bridge/types";
import { useStore } from "../../state/store";

interface Props {
  layer: LayerDescriptor;
  onClose: () => void;
}

export function UnlockDialog({ layer, onClose }: Props): JSX.Element {
  const { unlockLayer, unlockLayerWithRecoveryKey } = useStore();
  const [mode, setMode] = useState<"password" | "recovery">("password");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  const submit = async (): Promise<void> => {
    if (!secret) return;
    setBusy(true);
    setFailed(false);
    try {
      if (mode === "password") await unlockLayer(layer.id, secret);
      else await unlockLayerWithRecoveryKey(layer.id, secret);
      setSecret("");
      onClose();
    } catch (error) {
      // Every failure looks the same, on purpose.
      setFailed(true);
      setBusy(false);
      setSecret("");
      if (!(error instanceof BridgeCallError)) throw error;
    }
  };

  return (
    <div className="dialog-backdrop" role="presentation">
      <div
        className="dialog dialog--neutral"
        role="dialog"
        aria-modal="true"
        aria-labelledby="unlock-title"
        onKeyDown={(event) => {
          if (event.key === "Escape") onClose();
        }}
      >
        <h2 id="unlock-title" className="dialog__title">
          <span aria-hidden="true">🔒</span> Unlock {layer.display_name}
        </h2>

        <div className="dialog__body">
          <label className="properties__field">
            <span className="label">
              {mode === "password" ? "Password" : "Recovery key"}
            </span>
            <input
              className="input"
              type={mode === "password" ? "password" : "text"}
              autoComplete="off"
              autoFocus
              value={secret}
              aria-label={
                mode === "password" ? "Layer password" : "Recovery key"
              }
              onChange={(event) => setSecret(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void submit();
              }}
            />
          </label>

          {failed && (
            <p
              className="composer__status composer__status--error"
              role="alert"
            >
              That did not unlock the layer. Try again.
            </p>
          )}

          <button
            type="button"
            className="button button--ghost"
            onClick={() => {
              setMode(mode === "password" ? "recovery" : "password");
              setSecret("");
              setFailed(false);
            }}
          >
            {mode === "password"
              ? "Use a recovery key instead"
              : "Use the password instead"}
          </button>
        </div>

        <div className="dialog__actions">
          <button
            type="button"
            className="button"
            onClick={onClose}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            type="button"
            className="button button--primary"
            disabled={!secret || busy}
            onClick={() => void submit()}
          >
            {busy ? "Unlocking…" : "Unlock"}
          </button>
        </div>
      </div>
    </div>
  );
}
