/**
 * Provider selection, with the policy verdict shown inline.
 *
 * Every provider says whether it is local or remote, and a provider the current
 * selection is not allowed to reach is disabled *with the reason* — the same
 * verdict Python would return, because the UI asked Python for it. A remote
 * provider is never the silent default: a local one is preferred, and choosing a
 * remote one is a visible act.
 */

import { useState } from "react";
import type { ProviderView } from "../../bridge/types";
import { useStore } from "../../state/store";
import { CredentialDialog } from "./CredentialDialog";

export function ProviderSelector(): JSX.Element {
  const { providers, providerId, setProvider, policy, keychainAvailable } =
    useStore();
  const [configuring, setConfiguring] = useState<ProviderView | null>(null);

  const selected = providers.find((p) => p.provider_id === providerId);

  return (
    <div className="providers">
      <span className="label">Provider</span>

      {providers.length === 0 && (
        <p className="providers__notice">Loading providers…</p>
      )}

      <ul className="providers__list">
        {providers.map((provider) => {
          const active = provider.provider_id === providerId;
          return (
            <li key={provider.provider_id} className="providers__item">
              <button
                type="button"
                className={`button ${active ? "button--primary" : ""}`}
                disabled={!provider.configured}
                title={provider.note}
                aria-pressed={active}
                onClick={() => void setProvider(provider.provider_id)}
              >
                <span>{provider.display_name}</span>
                <span
                  className={`tag ${provider.is_local ? "tag--public" : "tag--warning"}`}
                >
                  {provider.is_local ? "local" : "remote"}
                </span>
              </button>

              {provider.requires_api_key && (
                <button
                  type="button"
                  className="tree__action"
                  title={
                    provider.configured
                      ? "Replace the API key"
                      : "Add an API key to enable this provider"
                  }
                  onClick={() => setConfiguring(provider)}
                >
                  {provider.configured ? "🔑" : "＋🔑"}
                </button>
              )}
            </li>
          );
        })}
      </ul>

      {selected && (
        <p className="providers__note">
          {selected.note} Context window:{" "}
          {selected.max_context_tokens.toLocaleString()} tokens.
        </p>
      )}

      {policy && policy.verdict !== "allowed" && (
        <p
          className={`providers__policy providers__policy--${policy.verdict}`}
          role="status"
        >
          <span
            className={`tag ${policy.verdict === "denied" ? "tag--danger" : "tag--warning"}`}
          >
            {policy.verdict === "denied" ? "Blocked" : "Needs confirmation"}
          </span>
          {policy.reason}
        </p>
      )}

      {!keychainAvailable && (
        <p className="providers__notice">
          <span className="tag tag--warning">No keychain</span> This system has
          no secure credential store, so remote providers that need an API key
          cannot be configured. Strata will not write a key to a plain file.
        </p>
      )}

      {configuring && (
        <CredentialDialog
          provider={configuring}
          onClose={() => setConfiguring(null)}
        />
      )}
    </div>
  );
}
