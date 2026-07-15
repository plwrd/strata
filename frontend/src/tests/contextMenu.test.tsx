/**
 * The shell's own context menu.
 *
 * Right-click must produce Strata's menu, not the browser's: no Reload, real
 * actions only, options that follow the app's state, and full keyboard control.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { AppContextMenu } from "../app/ContextMenu";
import { useStore } from "../state/store";
import { installFakeBridge } from "./fakeBridge";

function layer(id: string, visibility: string, state: string) {
  return {
    id,
    display_name: id,
    visibility,
    state,
    sharing_mode: "personal",
    storage: "markdown",
    storage_version: 1,
    created_at: "",
    updated_at: "",
    color: "layer-public",
    ai_policy: {} as never,
  };
}

function openMenu(): void {
  fireEvent.contextMenu(document.body, { clientX: 40, clientY: 40 });
}

describe("AppContextMenu", () => {
  beforeEach(() => {
    installFakeBridge();
    useStore.setState({
      mode: "explore",
      dimension: "3d",
      selectedIds: [],
      layers: [layer("layer_a", "public", "mounted")] as never,
    });
  });

  it("replaces the browser menu and never offers Reload", () => {
    render(<AppContextMenu />);
    openMenu();

    expect(
      screen.getByRole("menu", { name: "Strata menu" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/reload/i)).toBeNull();
    expect(screen.queryByText(/view source/i)).toBeNull();
  });

  it("prevents the native menu outside editable surfaces", () => {
    render(<AppContextMenu />);
    const event = new MouseEvent("contextmenu", {
      bubbles: true,
      cancelable: true,
    });
    document.body.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
  });

  it("leaves the native menu alone inside inputs", () => {
    render(
      <div>
        <input aria-label="field" />
        <AppContextMenu />
      </div>,
    );
    const input = screen.getByLabelText("field");
    const event = new MouseEvent("contextmenu", {
      bubbles: true,
      cancelable: true,
    });
    input.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("switches the graph dimension through the store", async () => {
    render(<AppContextMenu />);
    openMenu();

    await userEvent.click(
      screen.getByRole("menuitem", { name: /Switch to 2D graph/ }),
    );
    expect(useStore.getState().dimension).toBe("2d");
    expect(screen.queryByRole("menu")).toBeNull(); // acted, then closed
  });

  it("offers Clear selection only when something is selected", () => {
    render(<AppContextMenu />);
    openMenu();
    expect(
      screen.queryByRole("menuitem", { name: /Clear selection/ }),
    ).toBeNull();

    fireEvent.keyDown(window, { key: "Escape" });
    useStore.setState({ selectedIds: ["n1", "n2"] });
    openMenu();
    expect(
      screen.getByRole("menuitem", { name: /Clear selection \(2\)/ }),
    ).toBeInTheDocument();
  });

  it("offers Lock all only when a private layer is unlocked", () => {
    render(<AppContextMenu />);
    openMenu();
    expect(screen.queryByRole("menuitem", { name: /Lock all/ })).toBeNull();

    fireEvent.keyDown(window, { key: "Escape" });
    useStore.setState({
      layers: [
        layer("layer_a", "public", "mounted"),
        layer("layer_p", "private", "unlocked"),
      ] as never,
    });
    openMenu();
    expect(
      screen.getByRole("menuitem", { name: /Lock all private layers/ }),
    ).toBeInTheDocument();
  });

  it("closes on Escape", () => {
    render(<AppContextMenu />);
    openMenu();
    expect(screen.getByRole("menu")).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
  });
});
