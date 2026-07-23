/**
 * The prompt library: pick a saved prompt to fill the editor, or save the
 * current one. Running a prompt is still the user's explicit Send — a saved
 * prompt is a template, never an autonomous action.
 */

import { useCallback, useEffect, useState } from "react";
import { bridge } from "../../bridge/client";
import type { SavedPrompt } from "../../bridge/types";
import { useStore } from "../../state/store";

const CATEGORIES = [
  "research",
  "summarization",
  "meeting-processing",
  "project-planning",
  "weekly-review",
  "decision-extraction",
  "writing",
  "technical-analysis",
  "learning",
  "content-generation",
  "other",
] as const;

export function PromptLibraryPanel(): JSX.Element {
  const { prompt, setPrompt } = useStore();
  const [prompts, setPrompts] = useState<SavedPrompt[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [name, setName] = useState("");
  const [category, setCategory] =
    useState<(typeof CATEGORIES)[number]>("other");

  const load = useCallback(async () => {
    try {
      const { prompts: loaded } = await bridge.ai.listPrompts();
      setPrompts(loaded);
      setError(null);
    } catch (loadError) {
      setError(
        loadError instanceof Error
          ? loadError.message
          : "Could not load saved prompts.",
      );
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const applyPrompt = async (promptId: string): Promise<void> => {
    if (!promptId) return;
    try {
      const { prompt: saved } = await bridge.ai.usePrompt(promptId);
      setPrompt(saved.prompt_text);
      await load();
    } catch (useError) {
      setError(
        useError instanceof Error ? useError.message : "Could not load it.",
      );
    }
  };

  const saveCurrent = async (): Promise<void> => {
    if (!name.trim() || !prompt.trim()) return;
    try {
      await bridge.ai.savePrompt({
        name: name.trim(),
        prompt_text: prompt,
        category,
      });
      setName("");
      setSaving(false);
      await load();
    } catch (saveError) {
      setError(
        saveError instanceof Error ? saveError.message : "Saving failed.",
      );
    }
  };

  return (
    <div className="prompt-library">
      <div className="prompt-library__row">
        <select
          className="select"
          aria-label="Saved prompts"
          defaultValue=""
          onChange={(event) => {
            void applyPrompt(event.target.value);
            event.target.value = "";
          }}
        >
          <option value="" disabled>
            {prompts.length
              ? `Saved prompts (${prompts.length})`
              : "No saved prompts"}
          </option>
          {prompts.map((entry) => (
            <option key={entry.id} value={entry.id}>
              {entry.name} · v{entry.version}
              {entry.usage_count ? ` · used ${entry.usage_count}×` : ""}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="button button--ghost"
          disabled={!prompt.trim()}
          aria-expanded={saving}
          onClick={() => setSaving((open) => !open)}
        >
          Save prompt…
        </button>
      </div>

      {saving && (
        <div className="prompt-library__save">
          <input
            className="input"
            value={name}
            placeholder="Name this prompt"
            aria-label="Prompt name"
            onChange={(event) => setName(event.target.value)}
          />
          <select
            className="select"
            value={category}
            aria-label="Prompt category"
            onChange={(event) =>
              setCategory(event.target.value as (typeof CATEGORIES)[number])
            }
          >
            {CATEGORIES.map((value) => (
              <option key={value} value={value}>
                {value.replace(/-/g, " ")}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="button button--primary"
            disabled={!name.trim()}
            onClick={() => void saveCurrent()}
          >
            Save
          </button>
        </div>
      )}

      {error && (
        <p className="composer__status composer__status--error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
