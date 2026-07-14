/** The context tray: every object that would be included, and why. */

import type { ContextPlan } from "../../bridge/types";

interface Props {
  plan: ContextPlan | null;
  planning: boolean;
  error: string | null;
  onRemove: (objectId: string) => void;
  onClear: () => void;
}

export function ContextSourceList({
  plan,
  planning,
  error,
  onRemove,
  onClear,
}: Props): JSX.Element {
  if (error) {
    return (
      <p className="composer__status composer__status--error" role="alert">
        {error}
      </p>
    );
  }

  if (planning && !plan) {
    return <p className="empty-state">Building the context preview…</p>;
  }

  if (!plan) {
    return (
      <p className="empty-state">
        The selected objects cannot be used as sources. Tags, folders and locked
        objects are not exportable on their own.
      </p>
    );
  }

  return (
    <div className="tray">
      <div className="tray__header">
        <span className="label">Context ({plan.sources.length} sources)</span>
        <button
          type="button"
          className="button button--ghost"
          onClick={onClear}
        >
          Clear
        </button>
      </div>

      <ul className="tray__list">
        {plan.sources.map((source) => (
          <li key={source.source_id} className="tray__item">
            <div className="tray__meta">
              <span className="mono tray__id">{source.source_id}</span>
              <span className="tray__source-title">{source.title}</span>
              <span
                className={`tag ${source.is_private ? "tag--private" : "tag--public"}`}
              >
                {source.layer_name}
              </span>
              {source.truncated && (
                <span className="tag tag--warning">summarised</span>
              )}
            </div>
            <button
              type="button"
              className="button button--ghost"
              aria-label={`Remove ${source.title} from the context`}
              onClick={() => onRemove(source.object_id)}
            >
              ✕
            </button>
          </li>
        ))}
      </ul>

      {plan.excluded_locked_count > 0 && (
        <p className="tray__notice">
          <span className="tag tag--locked">
            {plan.excluded_locked_count} object(s) in locked layers were
            excluded
          </span>
        </p>
      )}

      {plan.warnings.map((warning) => (
        <p key={warning} className="tray__notice tray__notice--warning">
          <span className="tag tag--warning">{warning}</span>
        </p>
      ))}
    </div>
  );
}
