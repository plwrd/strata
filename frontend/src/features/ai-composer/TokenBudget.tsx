/**
 * Token budget and splitting.
 *
 * The estimate is Python's (it renders the actual document and measures it), so
 * the number here is not a guess about a guess. When the context exceeds the
 * budget it is *split*, never truncated, and the panel says how many parts.
 */

import type { ContextPlan } from "../../bridge/types";

interface Props {
  plan: ContextPlan | null;
  budget: number | null;
  onBudgetChange: (budget: number | null) => void;
}

const PRESETS: { label: string; value: number | null }[] = [
  { label: "No limit", value: null },
  { label: "8k", value: 8_000 },
  { label: "32k", value: 32_000 },
  { label: "128k", value: 128_000 },
  { label: "200k", value: 200_000 },
];

export function TokenBudget({
  plan,
  budget,
  onBudgetChange,
}: Props): JSX.Element {
  const estimated = plan?.estimated_tokens ?? 0;
  const usage = budget ? Math.min(estimated / budget, 1) : 0;
  const over = budget !== null && estimated > budget;

  return (
    <div className="budget">
      <div className="budget__header">
        <span className="label">Token budget</span>
        <span className="mono budget__estimate">
          ~{estimated.toLocaleString()} tokens
          {budget ? ` / ${budget.toLocaleString()}` : ""}
        </span>
      </div>

      <div className="budget__presets" role="group" aria-label="Token budget">
        {PRESETS.map((preset) => (
          <button
            key={preset.label}
            type="button"
            className={`button ${budget === preset.value ? "button--primary" : ""}`}
            aria-pressed={budget === preset.value}
            onClick={() => onBudgetChange(preset.value)}
          >
            {preset.label}
          </button>
        ))}
      </div>

      {budget !== null && (
        <div
          className="budget__bar"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={budget}
          aria-valuenow={estimated}
          aria-label="Context window usage"
        >
          <div
            className={`budget__fill ${over ? "budget__fill--over" : ""}`}
            style={{ width: `${Math.round(usage * 100)}%` }}
          />
        </div>
      )}

      {over && plan && (
        <p className="budget__notice">
          <span className="tag tag--warning">
            Splitting into {plan.part_count} parts. Nothing is truncated.
          </span>
        </p>
      )}
    </div>
  );
}
