/**
 * Create a layer.
 *
 * The private path is deliberately not a one-click affair: choosing a password
 * that cannot be reset is a decision with consequences, and the dialog states them
 * before the user commits rather than after.
 */

import { useState } from "react";
import { BridgeCallError } from "../../bridge/client";
import { useStore } from "../../state/store";

interface Props {
  onClose: () => void;
  onCreated: (layerName: string, recoveryKey: string | null) => void;
}

const MIN_PASSWORD = 8;

export function CreateLayerDialog({ onClose, onCreated }: Props): JSX.Element {
  const createLayer = useStore((state) => state.createLayer);
  const [name, setName] = useState("");
  const [isPrivate, setIsPrivate] = useState(false);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [withRecoveryKey, setWithRecoveryKey] = useState(true);
  const [starterFolders, setStarterFolders] = useState("");
  const [firstNote, setFirstNote] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const passwordProblem = (): string | null => {
    if (!isPrivate) return null;
    if (password.length < MIN_PASSWORD) {
      return `A layer password must be at least ${MIN_PASSWORD} characters.`;
    }
    if (password !== confirm) return "The passwords do not match.";
    return null;
  };

  const problem = !name.trim() ? "Give the layer a name." : passwordProblem();

  const submit = async (): Promise<void> => {
    if (problem) return;
    setBusy(true);
    setError(null);
    const folders = starterFolders
      .split(",")
      .map((folder) => folder.trim())
      .filter(Boolean);
    try {
      const recoveryKey = await createLayer(
        name.trim(),
        isPrivate ? "private" : "public",
        isPrivate ? password : null,
        withRecoveryKey,
        { folders, firstNote },
      );
      // The password leaves this component and is never stored anywhere.
      setPassword("");
      setConfirm("");
      onCreated(name.trim(), recoveryKey);
    } catch (cause) {
      setError(
        cause instanceof BridgeCallError
          ? cause.message
          : "The layer could not be created.",
      );
      setBusy(false);
    }
  };

  return (
    <div className="dialog-backdrop" role="presentation">
      <div
        className="dialog dialog--neutral"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-layer-title"
        onKeyDown={(event) => {
          if (event.key === "Escape") onClose();
        }}
      >
        <h2 id="create-layer-title" className="dialog__title">
          New layer
        </h2>

        <div className="dialog__body">
          <label className="properties__field">
            <span className="label">Name</span>
            <input
              className="input"
              value={name}
              autoFocus
              onChange={(event) => setName(event.target.value)}
            />
          </label>

          <fieldset className="dialog__fieldset">
            <legend className="label">Visibility</legend>

            <label className="dialog__choice">
              <input
                type="radio"
                name="visibility"
                checked={!isPrivate}
                onChange={() => setIsPrivate(false)}
              />
              <span>
                <strong>Public</strong> — plain Markdown files on disk. Readable
                by anything on this machine, and by any editor you like.
              </span>
            </label>

            <label className="dialog__choice">
              <input
                type="radio"
                name="visibility"
                checked={isPrivate}
                onChange={() => setIsPrivate(true)}
              />
              <span>
                <strong>Private</strong> — encrypted. Filenames, folders,
                titles, tags and attachments are all opaque on disk. Nothing is
                readable without the password.
              </span>
            </label>
          </fieldset>

          {isPrivate && (
            <>
              <label className="properties__field">
                <span className="label">Password</span>
                <input
                  className="input"
                  type="password"
                  autoComplete="new-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
              </label>

              <label className="properties__field">
                <span className="label">Confirm password</span>
                <input
                  className="input"
                  type="password"
                  autoComplete="new-password"
                  value={confirm}
                  onChange={(event) => setConfirm(event.target.value)}
                />
              </label>

              <label className="dialog__choice">
                <input
                  type="checkbox"
                  checked={withRecoveryKey}
                  onChange={(event) => setWithRecoveryKey(event.target.checked)}
                />
                <span>
                  Generate a recovery key. It is shown once and opens the layer
                  if you forget the password.
                </span>
              </label>

              <p className="dialog__warning" role="note">
                <span className="tag tag--warning">No reset</span> Strata cannot
                recover this layer for you. There is no copy of the password and
                no copy of the recovery key. If you lose both, the contents are
                gone permanently.
              </p>
            </>
          )}

          <fieldset className="dialog__fieldset">
            <legend className="label">Start with</legend>

            <label className="properties__field">
              <span className="label">Folders (comma-separated, optional)</span>
              <input
                className="input"
                value={starterFolders}
                placeholder="Ideas, Research, Archive"
                onChange={(event) => setStarterFolders(event.target.value)}
              />
            </label>

            <label className="dialog__choice">
              <input
                type="checkbox"
                checked={firstNote}
                onChange={(event) => setFirstNote(event.target.checked)}
              />
              <span>
                Create a first note, so the layer opens ready to write in.
              </span>
            </label>
          </fieldset>

          {error && (
            <p
              className="composer__status composer__status--error"
              role="alert"
            >
              {error}
            </p>
          )}
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
            disabled={Boolean(problem) || busy}
            title={problem ?? undefined}
            onClick={() => void submit()}
          >
            {busy
              ? "Creating…"
              : isPrivate
                ? "Create encrypted layer"
                : "Create layer"}
          </button>
        </div>
      </div>
    </div>
  );
}
