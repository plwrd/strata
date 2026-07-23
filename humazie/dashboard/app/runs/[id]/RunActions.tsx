"use client";

import { useState } from "react";

export function RunActions(props: {
  runId: string;
  flows: Array<{ id: string; name: string }>;
}): JSX.Element {
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState("");

  const rerunFailed = async (): Promise<void> => {
    setBusy(true);
    setLog("Rerunning failed flows…");
    try {
      const res = await fetch("/api/review", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "rerun-failed",
          runId: props.runId,
        }),
      });
      const data = (await res.json()) as { message?: string; error?: string };
      setLog(data.message ?? data.error ?? "Done");
    } catch (error) {
      setLog(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const rerunOne = async (flowId: string): Promise<void> => {
    setBusy(true);
    setLog(`Rerunning ${flowId}…`);
    try {
      const res = await fetch("/api/review", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "rerun", flowId }),
      });
      const data = (await res.json()) as { message?: string; error?: string };
      setLog(data.message ?? data.error ?? "Done");
    } catch (error) {
      setLog(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ display: "grid", gap: "0.5rem", justifyItems: "end" }}>
      <button type="button" disabled={busy} onClick={() => void rerunFailed()}>
        Rerun failed
      </button>
      <select
        aria-label="Rerun a single flow"
        disabled={busy}
        defaultValue=""
        onChange={(event) => {
          if (event.target.value) void rerunOne(event.target.value);
        }}
      >
        <option value="">Rerun one flow…</option>
        {props.flows.map((flow) => (
          <option key={flow.id} value={flow.id}>
            {flow.name}
          </option>
        ))}
      </select>
      {log ? <p className="mono muted">{log}</p> : null}
    </div>
  );
}
