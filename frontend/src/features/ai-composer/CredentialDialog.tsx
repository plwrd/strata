/**
 * Enter an API key.
 *
 * The key goes straight to Python, which puts it in the OS keychain and reports
 * back only whether that succeeded. It is never stored in the frontend, never
 * logged, and never echoed. If the machine has no keychain, Python refuses to
 * store it rather than writing it to a file, and this dialog says so.
 */

import { useState } from "react";
import type { ProviderView } from "../../bridge/types";
import { useStore } from "../../state/store";

interface Props {
  provider: ProviderView;
  onClose: () => void;
}

export function CredentialDialog({ provider, onClose }: Props): JSX.Element {
  const storeCredential = useStore((state) => state.storeCredential);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (): Promise<void> => {
    if (!key.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const stored = await storeCredential(provider.provider_id, key.trim());
      // Clear the key from component state whatever happened: it has left for the
      // keychain, and there is no reason to keep a copy in a React state object.
      setKey("");
      if (stored) {
        onClose();
      } else {
        setError(
          "The key was not stored. This system may have no secure keychain, and Strata will " +
            "not fall back to writing it in a file.",
        );
        setBusy(false);
      }
    } catch {
      setKey("");
      setError("The key could not be stored.");
      setBusy(false);
    }
  };

  return (
    <div className="dialog-backdrop" role="presentation">
      <div
        className="dialog dialog--neutral"
        role="dialog"
        aria-modal="true"
        aria-labelledby="cred-title"
        onKeyDown={(event) => {
          if (event.key === "Escape") onClose();
        }}
      >
        <h2 id="cred-title" className="dialog__title">
          API key for {provider.display_name}
        </h2>

        <div className="dialog__body">
          <p className="dialog__note">
            Stored in your operating system&apos;s keychain — never in a Strata
            file, a log, or an export.
          </p>

          <label className="properties__field">
            <span className="label">API key</span>
            <input
              className="input"
              type="password"
              autoComplete="off"
              autoFocus
              value={key}
              aria-label={`${provider.display_name} API key`}
              onChange={(event) => setKey(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void submit();
              }}
            />
          </label>

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
            disabled={!key.trim() || busy}
            onClick={() => void submit()}
          >
            {busy ? "Storing…" : "Store key"}
          </button>
        </div>
      </div>
    </div>
  );
}
