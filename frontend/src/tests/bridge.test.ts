/** The bridge client: envelope handling and the error contract. */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { installFakeBridge, type FakeBridgeOptions } from "./fakeBridge";

/**
 * The client memoises its WebChannel connection (connecting twice would register
 * two channels against one transport), so each test gets a fresh module instance.
 */
async function freshClient(options: FakeBridgeOptions = {}) {
  vi.resetModules();
  installFakeBridge(options);
  return import("../bridge/client");
}

describe("bridge client", () => {
  beforeEach(() => {
    window.qt = { webChannelTransport: {} };
  });

  it("reports availability", async () => {
    const { bridge } = await freshClient();
    expect(bridge.isAvailable()).toBe(true);
  });

  it("unwraps a successful envelope", async () => {
    const { bridge } = await freshClient();

    const health = await bridge.workspace.health();

    expect(health.ok).toBe(true);
    expect(health.protocol_version).toBe(1);
    expect(health.app).toBe("strata");
  });

  it("sends the protocol version and a unique request id", async () => {
    const seen: string[] = [];
    const { bridge } = await freshClient({
      onRequest: (_object, _method, raw) => seen.push(raw),
    });

    await bridge.workspace.health();
    await bridge.workspace.health();

    const [first, second] = seen.map(
      (raw) => JSON.parse(raw) as { v: number; requestId: string },
    );
    expect(first!.v).toBe(1);
    expect(first!.requestId).toMatch(/^req_/);
    expect(first!.requestId).not.toBe(second!.requestId);
  });

  it("sends the payload under a versioned envelope, not at the top level", async () => {
    const seen: string[] = [];
    const { bridge } = await freshClient({
      onRequest: (_object, _method, raw) => seen.push(raw),
    });

    await bridge.search.query("encryption", 10);

    const request = JSON.parse(seen[0]!) as {
      payload: Record<string, unknown>;
    };
    expect(request.payload).toEqual({ query: "encryption", limit: 10 });
  });

  it("turns an error envelope into a typed error carrying its code", async () => {
    const { bridge, BridgeCallError } = await freshClient({
      failWith: { code: "layer_locked", message: "This layer is locked." },
    });

    await expect(bridge.workspace.health()).rejects.toBeInstanceOf(
      BridgeCallError,
    );
    await expect(bridge.workspace.health()).rejects.toMatchObject({
      code: "layer_locked",
      message: "This layer is locked.",
    });
  });

  it("fails clearly when there is no host", async () => {
    vi.resetModules();
    window.qt = undefined;
    window.QWebChannel = undefined;
    const { bridge, BridgeUnavailableError } = await import("../bridge/client");

    expect(bridge.isAvailable()).toBe(false);
    await expect(bridge.workspace.health()).rejects.toBeInstanceOf(
      BridgeUnavailableError,
    );
  });
});
