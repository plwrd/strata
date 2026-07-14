import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./app/App";
import "./design-system/tokens.css";
import "./design-system/base.css";
import "./app/shell.css";

const container = document.getElementById("root");
if (!container)
  throw new Error("The #root element is missing from index.html.");

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
