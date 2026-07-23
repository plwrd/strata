"use client";

import { useState } from "react";

export default function NewReviewPage(): JSX.Element {
  const [route, setRoute] = useState("");
  const [mobile, setMobile] = useState(false);
  const [autoFix, setAutoFix] = useState(true);
  const [visual, setVisual] = useState(true);
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState("Idle.");
  const [runId, setRunId] = useState<string | null>(null);

  const start = async (): Promise<void> => {
    setBusy(true);
    setLog(
      visual
        ? "Starting visual review… a Chromium window will open so you can watch every click and keystroke."
        : "Starting headless review…",
    );
    try {
      const res = await fetch("/api/review", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "start",
          route: route || undefined,
          mobile,
          autoFix,
          visual,
        }),
      });
      const data = (await res.json()) as {
        runId?: string;
        reportPath?: string;
        message?: string;
        error?: string;
        log?: string;
      };
      if (!res.ok) {
        setLog(data.error ?? "Review failed");
        return;
      }
      setRunId(data.runId ?? null);
      setLog(data.log ?? data.message ?? "Review completed.");
    } catch (error) {
      setLog(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <h1>New product review</h1>
      <div className="panel">
        <label>
          Route / mode filter
          <input
            value={route}
            onChange={(event) => setRoute(event.target.value)}
            placeholder="#explore or capture"
          />
        </label>
        <label>
          <span>Viewport</span>
          <select
            value={mobile ? "mobile" : "desktop"}
            onChange={(event) => setMobile(event.target.value === "mobile")}
          >
            <option value="desktop">Desktop</option>
            <option value="mobile">Mobile</option>
          </select>
        </label>
        <label>
          <span>Watch mode</span>
          <select
            value={visual ? "visual" : "headless"}
            onChange={(event) => setVisual(event.target.value === "visual")}
          >
            <option value="visual">Visual — see clicks, typing, layout changes</option>
            <option value="headless">Headless — CI / background</option>
          </select>
        </label>
        <label>
          <span>Automatic fixes</span>
          <select
            value={autoFix ? "on" : "off"}
            onChange={(event) => setAutoFix(event.target.value === "on")}
          >
            <option value="on">Enabled (safe repairs only)</option>
            <option value="off">Disabled</option>
          </select>
        </label>
        <button
          type="button"
          className="primary"
          disabled={busy}
          onClick={() => void start()}
        >
          {busy ? "Running…" : "Start review"}
        </button>
      </div>

      <h2>Live log</h2>
      <pre className="logs">{log}</pre>
      {runId ? (
        <p>
          Open run: <a href={`/runs/${runId}`}>{runId}</a>
        </p>
      ) : null}
    </div>
  );
}
