/**
 * Search, with the "why this matched" explanation the ranker owes the user.
 *
 * Results are selectable, so search is a selection method for the composer like
 * any other surface.
 */

import { useStore } from "../../state/store";

export function SearchPanel(): JSX.Element {
  const {
    searchQuery,
    searchResults,
    searching,
    runSearch,
    selectMany,
    selectSearchResults,
    selectedIds,
  } = useStore();

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
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
