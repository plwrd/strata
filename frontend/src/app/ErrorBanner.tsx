import { useStore } from "../state/store";

/** Dismissible banner for runtime failures after the host is connected. */
export function ErrorBanner(): JSX.Element | null {
  const lastError = useStore((state) => state.lastError);
  const clearLastError = useStore((state) => state.clearLastError);

  if (!lastError) return null;

  return (
    <div className="error-banner" role="alert">
      <p className="error-banner__text">{lastError}</p>
      <button
        type="button"
        className="button button--ghost"
        onClick={clearLastError}
      >
        Dismiss
      </button>
    </div>
  );
}
