/**
 * Search, with the "why this matched" explanation the ranker owes the user.
 *
 * The reasons are generated from the signals that actually fired in Python, not
 * from a template that guesses. If a result shows "Semantically similar", the
 * semantic signal really did contribute to its score — the two cannot drift apart,
 * because one is derived from the other.
 *
 * Results are selectable, so search is a selection method for the AI composer like
 * any other surface.
 */

import { useState } from "react";
import { useStore } from "../../state/store";

const SIGNAL_LABELS: Record<string, string> = {
  lexical: "text",
  semantic: "meaning",
  tag: "tag",
  property: "property",
  graph: "linked",
  recency: "recent",
};

export function SearchPanel(): JSX.Element {
  const {
    searchQuery,
    searchResults,
    searching,
    semanticSearch,
    setSemanticSearch,
    runSearch,
    selectMany,
    selectSearchResults,
    selectedIds,
    findSimilar,
    activeNoteId,
  } = useStore();
  const [showSignals, setShowSignals] = useState(false);

  return (
    <section className="search" aria-label="Search">
      <h2 className="sidebar__heading">Search</h2>

      <input
        className="input"
        type="search"
        value={searchQuery}
        placeholder="Search titles, tags, properties, body…"
        aria-label="Search the workspace"
        onChange={(event) => void runSearch(event.target.value)}
      />

      <div className="search__options">
        <label className="search__toggle">
          <input
            type="checkbox"
            checked={semanticSearch}
            onChange={(event) => void setSemanticSearch(event.target.checked)}
          />
          <span>Semantic</span>
        </label>

        <label className="search__toggle">
          <input
            type="checkbox"
            checked={showSignals}
            onChange={(event) => setShowSignals(event.target.checked)}
          />
          <span>Show signals</span>
        </label>

        {activeNoteId && (
          <button
            type="button"
            className="button button--ghost"
            title="Notes similar to the one that is open"
            onClick={() => void findSimilar(activeNoteId)}
          >
            Similar to this
          </button>
        )}
      </div>

      {searching && <p className="empty-state">Searching…</p>}

      {!searching && searchQuery && searchResults.length === 0 && (
        <p className="empty-state">No results.</p>
      )}

      {searchResults.length > 0 && (
        <>
          <div className="search__actions">
            <span className="mono search__count">
              {searchResults.length} results
            </span>
            <button
              type="button"
              className="button button--ghost"
              onClick={selectSearchResults}
            >
              Select all
            </button>
          </div>

          <ul className="search__list">
            {searchResults.map((result) => (
              <li key={result.object_id}>
                <button
                  type="button"
                  className={`search__result ${selectedIds.includes(result.object_id) ? "search__result--selected" : ""}`}
                  aria-pressed={selectedIds.includes(result.object_id)}
                  onClick={(event) =>
                    selectMany(
                      [result.object_id],
                      event.ctrlKey || event.metaKey ? "add" : "replace",
                    )
                  }
                >
                  <span className="search__title">{result.title}</span>
                  <span className="search__snippet">{result.snippet}</span>

                  <ul className="search__reasons">
                    {result.reasons.map((reason) => (
                      <li key={reason} className="mono">
                        {reason}
                      </li>
                    ))}
                  </ul>

                  {showSignals && (
                    <ul
                      className="search__signals"
                      aria-label="Why this ranked here"
                    >
                      {Object.entries(result.signals)
                        .sort(([, a], [, b]) => b - a)
                        .map(([signal, value]) => (
                          <li key={signal} className="search__signal">
                            <span className="mono">
                              {SIGNAL_LABELS[signal] ?? signal}
                            </span>
                            <span
                              className="search__signal-bar"
                              style={{
                                width: `${Math.min(100, Math.round((value / (result.score || 1)) * 100))}%`,
                              }}
                              aria-hidden="true"
                            />
                            <span className="mono search__signal-value">
                              {value.toFixed(2)}
                            </span>
                          </li>
                        ))}
                    </ul>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
