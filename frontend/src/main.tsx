import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./app/App";
import { bridge } from "./bridge/client";
import { useStore } from "./state/store";
import "./design-system/tokens.css";
import "./design-system/base.css";
import "./app/shell.css";
import "./features/editor/editor.css";

/**
 * The store, exposed for the desktop-shell end-to-end tests.
 *
 * This is not an escalation of anything. `script-src 'self'` means no third-party
 * script can ever run in this page, so anything able to read `window` is already
 * our own bundle with full module-scope access. The store also holds no secrets:
 * every privileged decision lives in Python, behind the bridge.
 */
declare global {
  interface Window {
    __strataStore?: typeof useStore;
    __strataBridge?: typeof bridge;
  }
}
window.__strataStore = useStore;
window.__strataBridge = bridge;

const container = document.getElementById("root");
if (!container)
  throw new Error("The #root element is missing from index.html.");

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
