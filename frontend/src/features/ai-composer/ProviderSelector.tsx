/**
 * Provider selection.
 *
 * Every provider is listed with whether it is local or remote and whether it is
 * configured. Nothing here is a live control yet — the adapters arrive in
 * Milestone 7 — so the buttons are *disabled with a reason* rather than present
 * and inert. A control that looks live and does nothing is worse than an absent
 * one.
 */

import type { ProviderCapability } from "../../bridge/types";

interface Props {
  providers: ProviderCapability[];
}

export function ProviderSelector({ providers }: Props): JSX.Element {
  const configured = providers.filter((provider) => provider.configured);

  return (
    <div className="providers">
      <span className="label">Provider</span>

      {configured.length === 0 && (
        <p className="providers__notice">
          No AI provider is configured. You can still export a context package
          and paste it into any model. Direct requests arrive in Milestone 7.
        </p>
      )}

      <ul className="providers__list">
        {providers.map((provider) => (
          <li key={provider.provider_id} className="providers__item">
            <button
              type="button"
              className="button"
              disabled={!provider.configured}
              title={provider.note}
            >
              <span>{provider.display_name}</span>
              <span
                className={`tag ${provider.is_local ? "tag--public" : "tag--warning"}`}
              >
                {provider.is_local ? "local" : "remote"}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
