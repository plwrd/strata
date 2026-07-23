/**
 * The AI Context Composer.
 *
 * The product rule this component exists to enforce: *what you see is what gets
 * sent*. The plan rendered here is computed by Python from the same selection the
 * export uses, so the preview cannot drift from the payload. The component never
 * decides what is private, what is exportable, or how many tokens something costs
 * — it asks and it displays.
 */

import { useCallback, useState } from "react";
import { bridge, BridgeCallError } from "../../bridge/client";
import type {
  ContentMode,
  ContextDepth,
  ExportShape,
  ExportTarget,
} from "../../bridge/types";
import { summariseSelection, useStore } from "../../state/store";
import { AIHistoryPanel } from "./AIHistoryPanel";
import { ContextSourceList } from "./ContextSourceList";
import { PrivacyReview } from "./PrivacyReview";
import { PromptEditor } from "./PromptEditor";
import { ProviderSelector } from "./ProviderSelector";
import { ResponsePanel } from "./ResponsePanel";
import { TokenBudget } from "./TokenBudget";

const TARGETS: { value: ExportTarget; label: string }[] = [
  { value: "generic", label: "Generic Markdown" },
  { value: "chatgpt", label: "ChatGPT" },
  { value: "claude", label: "Claude" },
  { value: "gemini", label: "Gemini" },
  { value: "local", label: "Local model" },
];

const DEPTHS: { value: ContextDepth; label: string }[] = [
  { value: "selected-only", label: "Selected objects only" },
  { value: "plus-links", label: "Selected + outgoing links" },
  { value: "plus-backlinks", label: "Selected + backlinks" },
  { value: "one-hop", label: "One graph hop" },
  { value: "two-hops", label: "Two graph hops" },
];

const CONTENT_MODES: { value: ContentMode; label: string }[] = [
  { value: "full", label: "Full content" },
  { value: "summary", label: "Summarised" },
  { value: "titles-only", label: "Titles only" },
];

const SHAPES: { value: ExportShape; label: string }[] = [
  { value: "single-file", label: "Single Markdown file" },
  { value: "package", label: "Multi-file package" },
];

type Status =
  | { kind: "idle" }
  | { kind: "busy" }
  | { kind: "done"; message: string }
  | { kind: "error"; message: string };

export function AIComposerPanel(): JSX.Element {
  const state = useStore();
  const summary = summariseSelection(state);
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [reviewOpen, setReviewOpen] = useState(false);

  const plan = state.plan;
  const hasSources = (plan?.sources.length ?? 0) > 0;

  const request = useCallback(
    (acknowledgePrivate: boolean) => ({
      object_ids: state.selectedIds,
      prompt: state.prompt,
      target: state.target,
      shape: state.shape,
      depth: state.depth,
      content_mode: state.contentMode,
      token_budget: state.tokenBudget,
      acknowledge_private: acknowledgePrivate,
    }),
    [
      state.selectedIds,
      state.prompt,
      state.target,
      state.shape,
      state.depth,
      state.contentMode,
      state.tokenBudget,
    ],
  );

  const doExport = useCallback(
    async (kind: "clipboard" | "files", acknowledgePrivate: boolean) => {
      setStatus({ kind: "busy" });
      try {
        if (kind === "clipboard") {
          const { result } = await bridge.export.render(
            request(acknowledgePrivate),
          );
          const text = result.parts.map((part) => part.content).join("\n\n");
          await navigator.clipboard.writeText(text);
          setStatus({
            kind: "done",
            // Clipboard managers are a real leak path; say so rather than
            // pretending the copy is contained.
            message: `Copied ${result.parts.length} file(s) to the clipboard. Note that clipboard managers may retain this content.`,
          });
        } else {
          const written = await bridge.export.write(
            request(acknowledgePrivate),
          );
          setStatus({
            kind: "done",
            message: `Wrote ${written.files_written} file(s) into “${written.directory_name}”.`,
          });
        }
      } catch (error) {
        if (error instanceof BridgeCallError && error.code === "cancelled") {
          setStatus({ kind: "idle" });
          return;
        }
        if (
          error instanceof BridgeCallError &&
          error.code === "permission_denied"
        ) {
          setReviewOpen(true);
          setStatus({ kind: "idle" });
          return;
        }
        setStatus({
          kind: "error",
          message: error instanceof Error ? error.message : "Export failed.",
        });
      }
    },
    [request],
  );

  const startExport = (kind: "clipboard" | "files"): void => {
    if (plan?.private_source_count) {
      setReviewOpen(true);
      return;
    }
    void doExport(kind, false);
  };

  return (
    <section className="composer" aria-label="AI Context Composer">
      <header className="composer__header">
        <h2 className="composer__title">AI Context Composer</h2>
        <span className="tag tag--ai">{summary.count} selected</span>
      </header>

      {state.selectedIds.length === 0 ? (
        <div className="composer__body scroll-y">
          <p className="empty-state">
            Select nodes in the graph, the tree or the search results. Whatever
            you illuminate is exactly what a model would see — nothing more.
          </p>
          <AIHistoryPanel />
        </div>
      ) : (
        <div className="composer__body scroll-y">
          <ContextSourceList
            plan={plan}
            planning={state.planning}
            error={state.planError}
            onRemove={state.deselect}
            onClear={state.clearSelection}
          />

          <PromptEditor
            value={state.prompt}
            onChange={state.setPrompt}
            onCommit={state.refreshPlan}
          />

          <div className="composer__grid">
            <label className="composer__field">
              <span className="label">Target</span>
              <select
                className="select"
                value={state.target}
                onChange={(event) =>
                  state.setTarget(event.target.value as ExportTarget)
                }
              >
                {TARGETS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="composer__field">
              <span className="label">Shape</span>
              <select
                className="select"
                value={state.shape}
                onChange={(event) =>
                  state.setShape(event.target.value as ExportShape)
                }
              >
                {SHAPES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="composer__field">
              <span className="label">Context depth</span>
              <select
                className="select"
                value={state.depth}
                onChange={(event) =>
                  state.setDepth(event.target.value as ContextDepth)
                }
              >
                {DEPTHS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="composer__field">
              <span className="label">Content</span>
              <select
                className="select"
                value={state.contentMode}
                onChange={(event) =>
                  state.setContentMode(event.target.value as ContentMode)
                }
              >
                {CONTENT_MODES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <TokenBudget
            plan={plan}
            budget={state.tokenBudget}
            onBudgetChange={state.setTokenBudget}
          />

          <ProviderSelector />

          <ResponsePanel />

          <div className="composer__actions">
            <button
              type="button"
              className="button button--primary"
              disabled={!hasSources || status.kind === "busy"}
              onClick={() => startExport("files")}
            >
              Export Markdown…
            </button>
            <button
              type="button"
              className="button"
              disabled={!hasSources || status.kind === "busy"}
              onClick={() => startExport("clipboard")}
            >
              Copy to clipboard
            </button>
          </div>

          {status.kind === "done" && (
            <p className="composer__status composer__status--ok" role="status">
              {status.message}
            </p>
          )}
          {status.kind === "error" && (
            <p
              className="composer__status composer__status--error"
              role="alert"
            >
              {status.message}
            </p>
          )}

          <AIHistoryPanel />
        </div>
      )}

      {reviewOpen && plan && (
        <PrivacyReview
          plan={plan}
          onCancel={() => setReviewOpen(false)}
          onConfirm={(kind) => {
            setReviewOpen(false);
            void doExport(kind, true);
          }}
        />
      )}
    </section>
  );
}
