/**
 * Layers: create, unlock, lock, and key management.
 *
 * The security-relevant UI rules, in one place:
 *
 * * a password is typed, sent, and dropped — it is never held in the store, so a
 *   devtools inspection of application state cannot reveal it;
 * * the recovery key is shown exactly once, and the dialog will not close until
 *   the user confirms they have written it down, because there is no second copy;
 * * "change password" and "rotate key" are visibly different things, and the UI
 *   says which one actually revokes a leaked key;
 * * locking is always one click away, and Lock All is in the status bar.
 */

import { useState } from "react";
import { bridge, BridgeCallError } from "../../bridge/client";
import type { LayerAIPolicy, LayerDescriptor } from "../../bridge/types";
import { useStore } from "../../state/store";
import { CreateLayerDialog } from "./CreateLayerDialog";
import { KeyManagementDialog } from "./KeyManagementDialog";
import { RecoveryKeyDialog } from "./RecoveryKeyDialog";
import { UnlockDialog } from "./UnlockDialog";

const AI_ACCESS_OPTIONS: { value: LayerAIPolicy["access"]; label: string }[] = [
  { value: "disabled", label: "AI off" },
  { value: "local-only", label: "AI local" },
  { value: "remote-with-confirmation", label: "AI remote · ask" },
  { value: "remote-always", label: "AI remote" },
];

export function LayerPanel(): JSX.Element {
  const { layers, lockLayer, lockAllLayers, refreshLayers } = useStore();
  const [creating, setCreating] = useState(false);
  const [unlocking, setUnlocking] = useState<LayerDescriptor | null>(null);
  const [managing, setManaging] = useState<LayerDescriptor | null>(null);
  const [recoveryKey, setRecoveryKey] = useState<{
    layer: string;
    key: string;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const hasUnlocked = layers.some(
    (layer) => layer.visibility === "private" && layer.state === "unlocked",
  );

  const handleLock = async (layer: LayerDescriptor): Promise<void> => {
    try {
      await lockLayer(layer.id);
    } catch (cause) {
      setError(
        cause instanceof BridgeCallError
          ? cause.message
          : "Could not lock the layer.",
      );
    }
  };

  const setAccess = async (
    layer: LayerDescriptor,
    access: LayerAIPolicy["access"],
  ): Promise<void> => {
    try {
      // The policy is enforced in Python on every request; this select only
      // states the rule. Widening access never touches the other capability
      // flags, and a locked layer stays unreadable regardless.
      await bridge.layers.setAIPolicy(layer.id, {
        ...layer.ai_policy,
        access,
      });
      await refreshLayers();
    } catch (cause) {
      setError(
        cause instanceof BridgeCallError
          ? cause.message
          : "Could not change the AI policy.",
      );
    }
  };

  return (
    <section className="layers" aria-label="Layers">
      <div className="layers__header">
        <h2 className="sidebar__heading">Layers</h2>
        <div className="layers__header-actions">
          {hasUnlocked && (
            <button
              type="button"
              className="button button--ghost"
              title="Lock every private layer now"
              onClick={() => void lockAllLayers()}
            >
              Lock all
            </button>
          )}
          <button
            type="button"
            className="tree__action"
            title="New layer"
            onClick={() => setCreating(true)}
          >
            ＋
          </button>
        </div>
      </div>

      <ul className="layers__list">
        {layers.map((layer) => {
          const isPrivate = layer.visibility === "private";
          const locked = isPrivate && layer.state !== "unlocked";

          return (
            <li key={layer.id} className="layers__item">
              <div className="layers__item-main">
                <span
                  className={`layers__dot layers__dot--${locked ? "locked" : layer.visibility}`}
                  aria-hidden="true"
                />
                <span
                  className="layers__name"
                  title={`${layer.display_name} · ${locked ? "locked" : layer.visibility}`}
                >
                  {layer.display_name}
                </span>

                {locked ? (
                  <span className="tag tag--locked">locked</span>
                ) : (
                  <span className="visually-hidden">{layer.visibility}</span>
                )}

                {isPrivate && (
                  <span className="layers__actions">
                    {locked ? (
                      <button
                        type="button"
                        className="button button--ghost"
                        onClick={() => setUnlocking(layer)}
                      >
                        Unlock
                      </button>
                    ) : (
                      <>
                        <button
                          type="button"
                          className="tree__action"
                          title="Lock this layer now"
                          onClick={() => void handleLock(layer)}
                        >
                          🔒
                        </button>
                        <button
                          type="button"
                          className="tree__action"
                          title="Password, recovery key, and key rotation"
                          onClick={() => setManaging(layer)}
                        >
                          ⚙
                        </button>
                      </>
                    )}
                  </span>
                )}
              </div>

              <select
                className="select layers__ai-policy"
                aria-label={`AI policy for ${layer.display_name}`}
                title="What this layer's content may reach. Enforced in Python on every request; a locked layer is never readable regardless."
                value={layer.ai_policy?.access ?? "local-only"}
                onChange={(event) =>
                  void setAccess(
                    layer,
                    event.target.value as LayerAIPolicy["access"],
                  )
                }
              >
                {AI_ACCESS_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </li>
          );
        })}
      </ul>

      {error && (
        <p className="composer__status composer__status--error" role="alert">
          {error}
        </p>
      )}

      <p
        className="layers__hint"
        title="A locked layer contributes nothing to search, the graph, or AI."
      >
        Layers are permission boundaries — locked ones stay invisible.
      </p>

      {creating && (
        <CreateLayerDialog
          onClose={() => setCreating(false)}
          onCreated={(layerName, key) => {
            setCreating(false);
            if (key) setRecoveryKey({ layer: layerName, key });
          }}
        />
      )}

      {unlocking && (
        <UnlockDialog layer={unlocking} onClose={() => setUnlocking(null)} />
      )}

      {managing && (
        <KeyManagementDialog
          layer={managing}
          onClose={() => setManaging(null)}
          onRecoveryKey={(key) => {
            setManaging(null);
            setRecoveryKey({ layer: managing.display_name, key });
          }}
        />
      )}

      {recoveryKey && (
        <RecoveryKeyDialog
          layerName={recoveryKey.layer}
          recoveryKey={recoveryKey.key}
          onClose={() => setRecoveryKey(null)}
        />
      )}
    </section>
  );
}
