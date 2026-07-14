/**
 * Password, recovery key, and key rotation.
 *
 * These three look similar and are not. The dialog is explicit about the one thing
 * users routinely get wrong: **changing the password does not revoke a leaked
 * key.** If the layer key itself is out (a shared password, a former collaborator,
 * a stolen backup that was unlocked at the time), only rotation helps — and
 * rotation rewrites every object, so it is presented as the heavy operation it is.
 */

import { useState } from "react";
import { BridgeCallError } from "../../bridge/client";
import type { LayerDescriptor } from "../../bridge/types";
import { useStore } from "../../state/store";

interface Props {
  layer: LayerDescriptor;
  onClose: () => void;
  onRecoveryKey: (key: string) => void;
}

type Tab = "password" | "recovery" | "rotate";

export function KeyManagementDialog({
  layer,
  onClose,
  onRecoveryKey,
}: Props): JSX.Element {
  const { changeLayerPassword, reissueRecoveryKey, rotateLayerKey } =
    useStore();
  const [tab, setTab] = useState<Tab>("password");

  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [password, setPassword] = useState("");

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const fail = (cause: unknown, fallback: string): void => {
    setError(cause instanceof BridgeCallError ? cause.message : fallback);
    setBusy(false);
  };

  const clearSecrets = (): void => {
    setOldPassword("");
    setNewPassword("");
    setConfirm("");
    setPassword("");
  };

  const submitPassword = async (): Promise<void> => {
    if (newPassword.length < 8 || newPassword !== confirm) return;
    setBusy(true);
    setError(null);
    try {
      await changeLayerPassword(layer.id, oldPassword, newPassword);
      clearSecrets();
      setBusy(false);
      setDone("The password was changed. The layer key itself is unchanged.");
    } catch (cause) {
      fail(cause, "The password could not be changed.");
    }
  };

  const submitRecovery = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      const key = await reissueRecoveryKey(layer.id, password);
      clearSecrets();
      onRecoveryKey(key);
    } catch (cause) {
      fail(cause, "A new recovery key could not be issued.");
    }
  };

  const submitRotation = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      const count = await rotateLayerKey(layer.id, password);
      clearSecrets();
      setBusy(false);
      setDone(
        `Rotated. ${count} object(s) were re-encrypted under a new key. ` +
          `The old key no longer opens anything, and any recovery key was revoked.`,
      );
    } catch (cause) {
      fail(cause, "The key could not be rotated.");
    }
  };

  return (
    <div className="dialog-backdrop" role="presentation">
      <div
        className="dialog dialog--neutral"
        role="dialog"
        aria-modal="true"
        aria-labelledby="keys-title"
        onKeyDown={(event) => {
          if (event.key === "Escape") onClose();
        }}
      >
        <h2 id="keys-title" className="dialog__title">
          Keys for {layer.display_name}
        </h2>

        <div className="inspector__tabs" role="tablist">
          {(
            [
              ["password", "Change password"],
              ["recovery", "Recovery key"],
              ["rotate", "Rotate key"],
            ] as [Tab, string][]
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={tab === value}
              className={`inspector__tab ${tab === value ? "inspector__tab--active" : ""}`}
              onClick={() => {
                setTab(value);
                setError(null);
                setDone(null);
                clearSecrets();
              }}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="dialog__body">
          {tab === "password" && (
            <>
              <p className="dialog__note">
                Re-wraps the layer key with a new password. Instant, even on a
                large layer — and it does <strong>not</strong> revoke anyone who
                already has the key.
              </p>
              <label className="properties__field">
                <span className="label">Current password</span>
                <input
                  className="input"
                  type="password"
                  autoComplete="off"
                  value={oldPassword}
                  onChange={(event) => setOldPassword(event.target.value)}
                />
              </label>
              <label className="properties__field">
                <span className="label">New password</span>
                <input
                  className="input"
                  type="password"
                  autoComplete="new-password"
                  value={newPassword}
                  onChange={(event) => setNewPassword(event.target.value)}
                />
              </label>
              <label className="properties__field">
                <span className="label">Confirm new password</span>
                <input
                  className="input"
                  type="password"
                  autoComplete="new-password"
                  value={confirm}
                  onChange={(event) => setConfirm(event.target.value)}
                />
              </label>
              <button
                type="button"
                className="button button--primary"
                disabled={
                  busy || newPassword.length < 8 || newPassword !== confirm
                }
                onClick={() => void submitPassword()}
              >
                Change password
              </button>
            </>
          )}

          {tab === "recovery" && (
            <>
              <p className="dialog__note">
                Issues a new recovery key and revokes the old one. It is shown
                once.
              </p>
              <label className="properties__field">
                <span className="label">Layer password</span>
                <input
                  className="input"
                  type="password"
                  autoComplete="off"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
              </label>
              <button
                type="button"
                className="button button--primary"
                disabled={busy || !password}
                onClick={() => void submitRecovery()}
              >
                Issue a new recovery key
              </button>
            </>
          )}

          {tab === "rotate" && (
            <>
              <p className="dialog__warning">
                <span className="tag tag--danger">Heavy</span> Rotation
                generates a brand-new layer key and re-encrypts{" "}
                <strong>every object</strong> in the layer. This is the only
                operation that actually revokes a key someone else already has —
                a password change does not.
              </p>
              <p className="dialog__note">
                Back the layer up first. If the process is interrupted part-way,
                some objects will be under the new key and some under the old.
              </p>
              <p className="dialog__note">
                Any existing recovery key is revoked.
              </p>
              <label className="properties__field">
                <span className="label">Layer password</span>
                <input
                  className="input"
                  type="password"
                  autoComplete="off"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
              </label>
              <button
                type="button"
                className="button button--danger"
                disabled={busy || !password}
                onClick={() => void submitRotation()}
              >
                {busy ? "Re-encrypting…" : "Rotate the layer key"}
              </button>
            </>
          )}

          {error && (
            <p
              className="composer__status composer__status--error"
              role="alert"
            >
              {error}
            </p>
          )}
          {done && (
            <p className="composer__status composer__status--ok" role="status">
              {done}
            </p>
          )}
        </div>

        <div className="dialog__actions">
          <button type="button" className="button" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
