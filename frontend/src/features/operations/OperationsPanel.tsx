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

type Mode =
  "plan" | "notes" | "process" | "synthesize" | "refresh-project" | "weekly";

const NOTE_COUNT_CHOICES = [1, 2, 3, 5, 10];

const BUSY_LABEL: Record<Mode, string> = {
  plan: "Designing a plan…",
  notes: "Writing notes…",
  process: "Processing…",
  synthesize: "Synthesising…",
  "refresh-project": "Refreshing…",
  weekly: "Reviewing the week…",
};

const READY_LABEL: Record<Mode, string> = {
  plan: "Propose changes",
  notes: "Generate notes",
  process: "Process into knowledge",
  synthesize: "Synthesize",
  "refresh-project": "Refresh project memory",
  weekly: "Generate review",
};

const SYNTHESIS_KINDS: { value: string; label: string }[] = [
  { value: "summary", label: "Summary" },
  { value: "concept", label: "Concept page" },
  { value: "comparison", label: "Comparison" },
  { value: "research-brief", label: "Research brief" },
  { value: "project-plan", label: "Project plan" },
  { value: "faq", label: "FAQ" },
  { value: "timeline", label: "Timeline" },
];

export function OperationsPanel(): JSX.Element {
  const state = useStore();
  const summary = summariseSelection(state);
  const [prompt, setPrompt] = useState("");
  const [mode, setMode] = useState<Mode>("plan");
  // 0 means "let the model decide how many notes the material splits into".
  const [noteCount, setNoteCount] = useState(0);
  const [synthesisKind, setSynthesisKind] = useState("summary");
  const [meetingProfile, setMeetingProfile] = useState(false);
  const [reviewDays, setReviewDays] = useState(7);
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
    const provider = state.providerId;
    const model = state.model || "default";
    try {
      if (mode === "process") {
        // Process into knowledge works on the selection, not a prompt: the
        // extraction instructions live in Python, next to their validation.
        const { request_id } = await bridge.operations.processNotes({
          provider_id: provider,
          model,
          note_ids: state.selectedIds,
          confirmed_remote: false,
          profile: meetingProfile ? "meeting" : "general",
        });
        pendingRef.current = request_id;
        setPhase({ kind: "generating", requestId: request_id });
        return;
      }
      if (mode === "refresh-project") {
        const { request_id } = await bridge.operations.refreshProject({
          provider_id: provider,
          model,
          note_id: state.selectedIds[0] ?? "",
          confirmed_remote: false,
        });
        pendingRef.current = request_id;
        setPhase({ kind: "generating", requestId: request_id });
        return;
      }
      if (mode === "weekly") {
        const { request_id } = await bridge.operations.generateWeekly({
          provider_id: provider,
          model,
          days: reviewDays,
          confirmed_remote: false,
        });
        pendingRef.current = request_id;
        setPhase({ kind: "generating", requestId: request_id });
        return;
      }
      if (mode === "synthesize") {
        const { request_id } = await bridge.operations.synthesizeNotes({
          provider_id: provider,
          model,
          note_ids: state.selectedIds,
          kind: synthesisKind,
          confirmed_remote: false,
        });
        pendingRef.current = request_id;
        setPhase({ kind: "generating", requestId: request_id });
        return;
      }
      if (!prompt.trim()) return;
      const { request_id } = await bridge.operations.generate({
        provider_id: provider,
        model,
        prompt,
        object_ids: state.selectedIds,
        layer_ids: layerIds,
        confirmed_remote: false,
        mode,
        note_count: mode === "notes" ? noteCount : 0,
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
        <label className="operations__mode">
          <span className="label">What should the AI do?</span>
          <select
            className="select"
            value={mode}
            aria-label="Generation mode"
            onChange={(event) => setMode(event.target.value as Mode)}
          >
            <option value="plan">Reorganise the workspace</option>
            <option value="notes">Generate new notes</option>
            <option value="process">Process into knowledge</option>
            <option value="synthesize">Synthesize selection</option>
            <option value="refresh-project">Refresh project memory</option>
            <option value="weekly">Weekly review</option>
          </select>
        </label>

        {mode === "refresh-project" && (
          <p className="operations__hint">
            {summary.noteCount === 1
              ? "Compares the selected page with the notes that link to and from it, and proposes an updated page. The update is shown as a full before/after diff and is never pre-approved."
              : "Select exactly one project (or any) note to refresh from its neighbourhood."}
          </p>
        )}

        {mode === "weekly" && (
          <>
            <label className="operations__mode">
              <span className="label">Review window</span>
              <select
                className="select"
                value={reviewDays}
                aria-label="Review window"
                onChange={(event) => setReviewDays(Number(event.target.value))}
              >
                <option value={7}>Last 7 days</option>
                <option value={14}>Last 14 days</option>
                <option value={30}>Last 30 days</option>
              </select>
            </label>
            <p className="operations__hint">
              Reviews everything created or changed in the window and saves a
              cited weekly-review note in Reports/ — what was learned, decided,
              completed, left unresolved, and what to promote from the Inbox.
            </p>
          </>
        )}

        {mode === "synthesize" && (
          <>
            <label className="operations__mode">
              <span className="label">Synthesis kind</span>
              <select
                className="select"
                value={synthesisKind}
                aria-label="Synthesis kind"
                onChange={(event) => setSynthesisKind(event.target.value)}
              >
                {SYNTHESIS_KINDS.map((entry) => (
                  <option key={entry.value} value={entry.value}>
                    {entry.label}
                  </option>
                ))}
              </select>
            </label>
            <p className="operations__hint">
              {summary.noteCount >= 2
                ? `Combines ${summary.noteCount} selected note(s) into one cited document in Reports/. Source notes are never modified; invented citations are stripped and reported.`
                : "Select at least two notes to synthesise."}
            </p>
          </>
        )}

        {mode === "process" && (
          <>
            <p className="operations__hint">
              {summary.noteCount > 0
                ? `Extracts concepts, entities, decisions, tasks and tags from ${summary.noteCount} selected note(s), as a plan you review. New pages are marked ai-inferred until you verify them.`
                : "Select the captures or notes to process — nothing is extracted from an empty selection."}
            </p>
            <label className="operations__meeting">
              <input
                type="checkbox"
                checked={meetingProfile}
                onChange={(event) => setMeetingProfile(event.target.checked)}
              />
              <span>
                Treat as meeting transcript — extract participants, decisions
                and action items, each anchored to its verbatim passage
              </span>
            </label>
          </>
        )}

        {mode === "notes" && (
          <label className="operations__mode">
            <span className="label">Number of notes</span>
            <select
              className="select"
              value={noteCount}
              aria-label="Number of notes"
              onChange={(event) => setNoteCount(Number(event.target.value))}
            >
              <option value={0}>Let the AI decide</option>
              {NOTE_COUNT_CHOICES.map((count) => (
                <option key={count} value={count}>
                  {count}
                </option>
              ))}
            </select>
          </label>
        )}

        {mode === "notes" && (
          <p className="operations__hint">
            {summary.noteCount > 0
              ? `Generating from ${summary.noteCount} selected note(s) — their content is shared with the model as context.`
              : "No note selected — the AI writes from your prompt alone. Select a note to generate from it."}
          </p>
        )}

        {(mode === "plan" || mode === "notes") && (
          <>
            <span className="label">
              {mode === "notes"
                ? "Describe the notes to generate"
                : "Ask the AI to reorganise or generate"}
            </span>
            <textarea
              className="textarea"
              value={prompt}
              placeholder={
                mode === "notes"
                  ? "e.g. Split this note into one note per topic, or write a study guide as several notes."
                  : "e.g. Create a folder structure for a research project on encryption, with starter notes."
              }
              aria-label="Operation prompt"
              onChange={(event) => setPrompt(event.target.value)}
            />
          </>
        )}
        <button
          type="button"
          className="button button--ai"
          disabled={
            phase.kind === "generating" ||
            (mode === "process"
              ? summary.noteCount === 0
              : mode === "synthesize"
                ? summary.noteCount < 2
                : mode === "refresh-project"
                  ? summary.noteCount !== 1
                  : mode === "weekly"
                    ? false
                    : !prompt.trim())
          }
          onClick={() => void generate()}
        >
          {phase.kind === "generating" ? BUSY_LABEL[mode] : READY_LABEL[mode]}
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
