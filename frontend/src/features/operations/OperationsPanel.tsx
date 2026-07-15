/**
 * The transactional AI change engine, in the UI.
 *
 * The flow the user sees mirrors the one Python enforces: describe a change, get a
 * plan, review it as a diff, tick the operations to apply, apply them in a
 * transaction, and undo if it was not what you wanted. Nothing is applied that the
 * user did not tick, and a destructive operation is visibly marked so it cannot be
 * approved by accident in a wall of green.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { bridge, BridgeCallError } from "../../bridge/client";
import type {
  OperationPlan,
  PlanReview,
  PlanStreamEvent,
} from "../../bridge/types";
import { summariseSelection, useStore } from "../../state/store";

type Phase =
  | { kind: "idle" }
  | { kind: "generating"; requestId: string }
  | { kind: "review"; review: PlanReview; approved: Set<number> }
  | { kind: "applied"; planId: string; summary: string }
  | { kind: "error"; message: string };

export function OperationsPanel(): JSX.Element {
  const state = useStore();
  const summary = summariseSelection(state);
  const [prompt, setPrompt] = useState("");
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });

  const layerIds = useMemo(
    () =>
      summary.layerIds.length
        ? summary.layerIds
        : state.layers.map((l) => l.id),
    [summary.layerIds, state.layers],
  );
  const layerIdsRef = useRef(layerIds);
  layerIdsRef.current = layerIds;

  // The request id we are currently waiting on. A ref, not state, so the event
  // handler reads the latest value without re-subscribing.
  const pendingRef = useRef<string | null>(null);

  const reviewPlan = async (plan: OperationPlan): Promise<void> => {
    try {
      const { review } = await bridge.operations.review(
        plan,
        layerIdsRef.current,
      );
      setPhase({
        kind: "review",
        review,
        // Pre-tick every valid, non-destructive operation; leave destructive ones
        // for the user to opt into deliberately.
        approved: new Set(
          review.entries
            .filter((e) => e.valid && !e.is_destructive)
            .map((e) => e.index),
        ),
      });
    } catch (error) {
      setPhase({ kind: "error", message: describe(error) });
    }
  };

  // Plan generation streams a completion over planEvent; a bad model answer is an
  // empty plan, not a crash. The side-effecting review runs *outside* the state
  // updater, keyed off the request id we are waiting on.
  useEffect(() => {
    void bridge.operations.onPlan((raw) => {
      const event = JSON.parse(raw) as PlanStreamEvent;
      if (event.requestId !== pendingRef.current) return;
      pendingRef.current = null;
      if (event.kind === "error" || !event.plan) {
        setPhase({
          kind: "error",
          message: event.error ?? "The model did not return a plan.",
        });
        return;
      }
      void reviewPlan(event.plan);
    });
  }, []);

  const generate = async (): Promise<void> => {
    if (!prompt.trim()) return;
    const provider = state.providerId;
    const model = state.model || "default";
    try {
      const { request_id } = await bridge.operations.generate({
        provider_id: provider,
        model,
        prompt,
        object_ids: state.selectedIds,
        layer_ids: layerIds,
        confirmed_remote: false,
      });
      pendingRef.current = request_id;
      setPhase({ kind: "generating", requestId: request_id });
    } catch (error) {
      setPhase({ kind: "error", message: describe(error) });
    }
  };

  const apply = async (): Promise<void> => {
    if (phase.kind !== "review") return;
    const approved = [...phase.approved];
    try {
      const { applied } = await bridge.operations.apply(
        phase.review.plan,
        approved,
        layerIds,
      );
      setPhase({
        kind: "applied",
        planId: applied.plan_id,
        summary: phase.review.plan.summary,
      });
      await state.reloadTree();
      await state.reloadGraph();
    } catch (error) {
      setPhase({ kind: "error", message: describe(error) });
    }
  };

  const undo = async (): Promise<void> => {
    if (phase.kind !== "applied") return;
    try {
      await bridge.operations.undo(phase.planId);
      setPhase({ kind: "idle" });
      await state.reloadTree();
      await state.reloadGraph();
    } catch (error) {
      setPhase({ kind: "error", message: describe(error) });
    }
  };

  const toggle = (index: number): void => {
    if (phase.kind !== "review") return;
    const approved = new Set(phase.approved);
    if (approved.has(index)) approved.delete(index);
    else approved.add(index);
    setPhase({ ...phase, approved });
  };

  return (
    <section className="operations" aria-label="AI operations">
      <div className="operations__prompt">
        <span className="label">Ask the AI to reorganise or generate</span>
        <textarea
          className="textarea"
          value={prompt}
          placeholder="e.g. Create a folder structure for a research project on encryption, with starter notes."
          aria-label="Operation prompt"
          onChange={(event) => setPrompt(event.target.value)}
        />
        <button
          type="button"
          className="button button--ai"
          disabled={!prompt.trim() || phase.kind === "generating"}
          onClick={() => void generate()}
        >
          {phase.kind === "generating"
            ? "Designing a plan…"
            : "Propose changes"}
        </button>
      </div>

      {phase.kind === "error" && (
        <p className="composer__status composer__status--error" role="alert">
          {phase.message}
        </p>
      )}

      {phase.kind === "review" && (
        <div className="operations__review">
          <div className="operations__review-header">
            <h3 className="sidebar__heading">
              {phase.review.plan.summary || "Proposed plan"}
            </h3>
            <span className="mono">
              {phase.approved.size} of {phase.review.valid_count} approved
            </span>
          </div>

          {phase.review.warnings.map((warning) => (
            <p key={warning} className="operations__warning">
              <span className="tag tag--warning">{warning}</span>
            </p>
          ))}

          <ul className="operations__list">
            {phase.review.entries.map((entry) => (
              <li
                key={entry.index}
                className={`operations__op ${!entry.valid ? "operations__op--invalid" : ""} ${
                  entry.is_destructive ? "operations__op--destructive" : ""
                }`}
              >
                <label className="operations__op-check">
                  <input
                    type="checkbox"
                    checked={phase.approved.has(entry.index)}
                    disabled={!entry.valid}
                    onChange={() => toggle(entry.index)}
                  />
                </label>
                <div className="operations__op-body">
                  <div className="operations__op-title">
                    <span className="mono operations__op-type">
                      {entry.type.replace(/_/g, " ")}
                    </span>
                    {entry.is_destructive && (
                      <span className="tag tag--danger">changes content</span>
                    )}
                    {entry.is_private && (
                      <span className="tag tag--private">
                        {entry.layer_name}
                      </span>
                    )}
                    {!entry.valid && (
                      <span className="tag tag--warning">{entry.problem}</span>
                    )}
                  </div>
                  <div className="operations__op-summary">{entry.summary}</div>
                  {entry.rationale && (
                    <div className="operations__op-rationale">
                      {entry.rationale}
                    </div>
                  )}
                  {(entry.before || entry.after) && (
                    <div className="operations__diff">
                      {entry.before && (
                        <div className="operations__diff-before">
                          − {entry.before}
                        </div>
                      )}
                      {entry.after && (
                        <div className="operations__diff-after">
                          + {entry.after}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ul>

          <div className="operations__actions">
            <button
              type="button"
              className="button button--primary"
              disabled={phase.approved.size === 0}
              onClick={() => void apply()}
            >
              Apply {phase.approved.size} change(s)
            </button>
            <button
              type="button"
              className="button button--ghost"
              onClick={() => setPhase({ kind: "idle" })}
            >
              Reject all
            </button>
          </div>
        </div>
      )}

      {phase.kind === "applied" && (
        <div className="operations__applied" role="status">
          <p className="composer__status composer__status--ok">
            Applied “{phase.summary}”. A snapshot was taken first, so this is
            reversible.
          </p>
          <button type="button" className="button" onClick={() => void undo()}>
            Undo
          </button>
        </div>
      )}
    </section>
  );
}

function describe(error: unknown): string {
  if (error instanceof BridgeCallError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}
