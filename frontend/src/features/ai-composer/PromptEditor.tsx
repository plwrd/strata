/** The prompt field, with slash-command templates. */

import { useState } from "react";

interface Props {
  value: string;
  onChange: (value: string) => void;
  onCommit: () => Promise<void>;
}

// Slash commands are prompt *templates*, not instructions to Python. Expanding one
// only fills the textarea; the user still reviews and sends it.
const SLASH_COMMANDS: {
  command: string;
  description: string;
  template: string;
}[] = [
  {
    command: "/summarize",
    description: "Summarise the selected knowledge",
    template:
      "Summarise the selected sources. Lead with the through-line, then the notable details. Cite every claim with its source ID.",
  },
  {
    command: "/compare",
    description: "Compare the selected sources",
    template:
      "Compare the selected sources. Where do they agree, where do they disagree, and what does the disagreement turn on? Cite source IDs.",
  },
  {
    command: "/find-gaps",
    description: "Find what is missing",
    template:
      "Identify what is missing from the selected sources: unanswered questions, unstated assumptions, and claims made without evidence. Do not invent facts to fill the gaps.",
  },
  {
    command: "/create-structure",
    description: "Propose a folder and note structure",
    template:
      "Propose a folder structure and a set of notes that would organise this material well. Explain the reasoning for each top-level folder.",
  },
  {
    command: "/create-tasks",
    description: "Extract actionable tasks",
    template:
      "Extract the actionable tasks implied by the selected sources. For each: the task, the source ID it came from, and what would make it done.",
  },
  {
    command: "/suggest-links",
    description: "Suggest relationships",
    template:
      "Suggest typed relationships between the selected sources (supports, contradicts, depends on, expands, supersedes). Give a one-line justification and the source IDs for each.",
  },
  {
    command: "/create-prd",
    description: "Draft a product requirements document",
    template:
      "Draft a product requirements document from the selected sources. Mark every requirement that is grounded in a source with its ID, and mark everything else as a recommendation.",
  },
  {
    command: "/create-architecture",
    description: "Draft an architecture document",
    template:
      "Draft an architecture document from the selected sources: components, boundaries, data flow, and the decisions that are already settled versus still open.",
  },
];

export function PromptEditor({
  value,
  onChange,
  onCommit,
}: Props): JSX.Element {
  const [showCommands, setShowCommands] = useState(false);

  return (
    <div className="prompt">
      <div className="prompt__header">
        <span className="label">Prompt</span>
        <button
          type="button"
          className="button button--ghost"
          aria-expanded={showCommands}
          onClick={() => setShowCommands((open) => !open)}
        >
          / commands
        </button>
      </div>

      {showCommands && (
        <ul className="prompt__commands">
          {SLASH_COMMANDS.map((entry) => (
            <li key={entry.command}>
              <button
                type="button"
                className="prompt__command"
                onClick={() => {
                  onChange(entry.template);
                  setShowCommands(false);
                  void onCommit();
                }}
              >
                <span className="mono">{entry.command}</span>
                <span className="prompt__command-description">
                  {entry.description}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      <textarea
        className="textarea"
        value={value}
        placeholder="Ask something about the selected knowledge…"
        aria-label="Prompt"
        onChange={(event) => onChange(event.target.value)}
        onBlur={() => void onCommit()}
      />
    </div>
  );
}
