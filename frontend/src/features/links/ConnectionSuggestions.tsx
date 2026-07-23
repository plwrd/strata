/**
 * Discovered connections for the open note — computed suggestions, each with a
 * visible reason, and never applied without the user's click.
 *
 * Accepting builds a one-operation plan (add_relationship) and pushes it
 * through the same review/apply engine as every AI change: snapshot-backed,
 * audited, undoable. Dismissing is local — a dismissed offer just goes away.
 */

import { useCallback, useEffect, useState } from "react";
import { bridge } from "../../bridge/client";
import type { ConnectionSuggestion } from "../../bridge/types";
import { useStore } from "../../state/store";

type LoadState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; suggestions: ConnectionSuggestion[] }
  | { kind: "error"; message: string };

const KIND_LABEL: Record<ConnectionSuggestion["kind"], string> = {
  similar: "similar",
  duplicate: "possible duplicate",
  mention: "unlinked mention",
};

export function ConnectionSuggestions(): JSX.Element | null {
  const { openNote, reloadOpenNote } = useStore();
  const noteId = openNote?.metadata.id ?? null;
  const [state, setState] = useState<LoadState>({ kind: "idle" });
  const [applied, setApplied] = useState<string | null>(null);

  const discover = useCallback(async (id: string) => {
    setState({ kind: "loading" });
    try {
      const { suggestions } = await bridge.graph.suggestConnections(id);
      setState({ kind: "ready", suggestions });
    } catch (error) {
      setState({
        kind: "error",
        message: error instanceof Error ? error.message : "Discovery failed.",
      });
    }
  }, []);

  useEffect(() => {
    setState({ kind: "idle" });
    setApplied(null);
  }, [noteId]);

  if (!noteId) return null;

  const accept = async (suggestion: ConnectionSuggestion): Promise<void> => {
    const plan = {
      id: `plan_connect_${suggestion.note_a}_${suggestion.note_b}`,
      summary: `Connect “${suggestion.note_a_title}” → “${suggestion.note_b_title}”`,
      operations: [
        {
          type: "add_relationship",
          layer_id: suggestion.layer_id,
          note_id: suggestion.note_a,
          folder_path: "",
          title: "",
          content: "",
          target_note_id: suggestion.note_b,
          target_title: suggestion.note_b_title,
          relationship: suggestion.suggested_relationship,
          property_key: "",
          property_value: "",
          tag: "",
          rationale: suggestion.explanation,
        },
      ],
      created_at: "",
      provider: "",
      model: "",
      prompt: "Discover connections",
    };
    try {
      const { review } = await bridge.operations.review(plan, [
        suggestion.layer_id,
      ]);
      const valid = review.entries.filter((entry) => entry.valid);
      if (valid.length === 0) {
        setState({
          kind: "error",
          message: review.entries[0]?.problem || "The connection is not valid.",
        });
        return;
      }
      await bridge.operations.apply(
        plan,
        valid.map((entry) => entry.index),
        [suggestion.layer_id],
      );
      setApplied(
        `${suggestion.note_a_title} ${suggestion.suggested_relationship.replace(/_/g, " ")} ${suggestion.note_b_title}`,
      );
      await reloadOpenNote();
      await discover(noteId);
    } catch (error) {
      setState({
        kind: "error",
        message: error instanceof Error ? error.message : "Connecting failed.",
      });
    }
  };

  const dismiss = (suggestion: ConnectionSuggestion): void => {
    if (state.kind !== "ready") return;
    setState({
      kind: "ready",
      suggestions: state.suggestions.filter(
        (entry) =>
          !(
            entry.note_a === suggestion.note_a &&
            entry.note_b === suggestion.note_b
          ),
      ),
    });
  };

  return (
    <section className="connections" aria-label="Suggested connections">
      <div className="connections__header">
        <h2 className="sidebar__heading">Suggested connections</h2>
        <button
          type="button"
          className="button button--ghost"
          disabled={state.kind === "loading"}
          onClick={() => void discover(noteId)}
        >
          {state.kind === "loading" ? "Discovering…" : "Discover"}
        </button>
      </div>

      {state.kind === "error" && (
        <p className="composer__status composer__status--error" role="alert">
          {state.message}
        </p>
      )}

      {applied && (
        <p className="composer__status composer__status--ok" role="status">
          Added: {applied}. Undoable from the Changes tab.
        </p>
      )}

      {state.kind === "ready" && state.suggestions.length === 0 && (
        <p className="empty-state">
          Nothing to suggest — this note's neighbourhood looks connected.
        </p>
      )}

      {state.kind === "ready" && state.suggestions.length > 0 && (
        <ul className="connections__list">
          {state.suggestions.map((suggestion) => (
            <li
              key={`${suggestion.note_a}:${suggestion.note_b}`}
              className="connections__item"
            >
              <div className="connections__meta">
                <span
                  className={`tag ${suggestion.kind === "duplicate" ? "tag--warning" : ""}`}
                >
                  {KIND_LABEL[suggestion.kind]}
                </span>
                <span className="connections__pair">
                  {suggestion.note_a_title} ↔ {suggestion.note_b_title}
                </span>
              </div>
              <p className="connections__why">{suggestion.explanation}</p>
              {suggestion.excerpt && (
                <p className="links__context">{suggestion.excerpt}</p>
              )}
              <div className="connections__actions">
                <button
                  type="button"
                  className="button"
                  onClick={() => void accept(suggestion)}
                >
                  Add “{suggestion.suggested_relationship.replace(/_/g, " ")}”
                </button>
                <button
                  type="button"
                  className="button button--ghost"
                  onClick={() => dismiss(suggestion)}
                >
                  Dismiss
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
