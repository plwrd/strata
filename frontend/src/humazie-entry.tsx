/**
 * Humazie Bot browser harness.
 *
 * Installs the Vitest fake bridge so Playwright can exercise the full shell
 * without Qt WebEngine or a real on-disk workspace. Product code is unchanged;
 * this entry is only loaded from humazie.html.
 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./app/App";
import { bridge } from "./bridge/client";
import { useStore } from "./state/store";
import { installFakeBridge } from "./tests/fakeBridge";
import "./design-system/tokens.css";
import "./design-system/base.css";
import "./app/shell.css";
import "./features/editor/editor.css";

declare global {
  interface Window {
    __strataStore?: typeof useStore;
    __strataBridge?: typeof bridge;
    __humazieHarness?: boolean;
  }
}

installFakeBridge();
window.__humazieHarness = true;
window.__strataStore = useStore;
window.__strataBridge = bridge;

// Prefer the list/2D path in the harness so Vite module workers are not required.
useStore.setState({ dimension: "2d" });

const container = document.getElementById("root");
if (!container) {
  throw new Error("The #root element is missing from humazie.html.");
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
