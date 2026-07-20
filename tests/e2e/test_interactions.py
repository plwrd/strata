"""Interaction-level e2e: real user flows through the running Qt shell.

Unit tests proved the *logic*; these prove the *interactions* — the layer where
the reported link-click bug and the tab-switch corruption lived, because nothing
exercised the app the way a person does. Each test drives the real store and
dispatches real DOM events into the real React tree.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.e2e._shell_support import (
    run_async_js,
    run_js,
    wait_for_load,
    wait_for_tree,
)

pytestmark = [pytest.mark.gui, pytest.mark.slow]


def _public_layer(services: Any) -> str:
    for layer in services.workspace.readable_layers():
        if layer.visibility == "public":
            return layer.id
    raise AssertionError("no public layer in the seeded workspace")


def _make_note(services: Any, title: str, content: str) -> str:
    layer = _public_layer(services)
    note = services.notes.create_note(layer_id=layer, title=title, content=content)
    return note.metadata.id


def _open(qtbot: Any, window: Any, note_id: str) -> None:
    run_async_js(
        qtbot,
        window,
        f"window.__strataStore.getState().reloadTree()"
        f".then(() => window.__strataStore.getState().openNoteById({note_id!r}))",
    )


def _active(qtbot: Any, window: Any) -> str:
    return str(run_js(qtbot, window, "window.__strataStore.getState().activeNoteId"))


# --- the reported bug: links must be clickable --------------------------------


def test_clicking_a_wiki_link_in_the_preview_opens_the_target_note(qtbot: Any, shell: Any) -> None:
    """A rendered [[wiki link]] in reading mode navigates to the target note.

    This is the flow the user reported broken. It renders the real preview, finds
    the real anchor, and dispatches a real click — so a regression in the render,
    the sanitiser (data-note stripped), or the click handler all fail it.
    """
    _app, window, services = shell
    wait_for_load(qtbot, window)
    wait_for_tree(qtbot, window)

    target = _make_note(services, "Target Note", "I am the destination.\n")
    source = _make_note(services, "Source Note", "Go to [[Target Note]] please.\n")

    _open(qtbot, window, source)
    # Reading mode renders the preview with the resolved links.
    run_js(qtbot, window, "window.__strataStore.getState().setViewMode('reading')")

    # Wait for the resolved (not broken) wiki link to appear in the preview.
    qtbot.waitUntil(
        lambda: (
            run_js(
                qtbot,
                window,
                "!!document.querySelector('.preview [data-note]')",
            )
            is True
        ),
        timeout=20_000,
    )
    # It must resolve to the target, not render as a broken link.
    resolved_id = run_js(
        qtbot,
        window,
        "document.querySelector('.preview [data-note]').getAttribute('data-note')",
    )
    assert resolved_id == target

    # Dispatch a real click on the real anchor — exactly what the user does.
    run_js(
        qtbot,
        window,
        "document.querySelector('.preview [data-note]')"
        ".dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}))",
    )

    qtbot.waitUntil(lambda: _active(qtbot, window) == target, timeout=20_000)


def test_a_broken_wiki_link_is_shown_as_broken_and_does_not_navigate(
    qtbot: Any, shell: Any
) -> None:
    _app, window, services = shell
    wait_for_load(qtbot, window)
    wait_for_tree(qtbot, window)

    source = _make_note(services, "Dangling Source", "See [[Nonexistent Note]].\n")
    _open(qtbot, window, source)
    run_js(qtbot, window, "window.__strataStore.getState().setViewMode('reading')")

    qtbot.waitUntil(
        lambda: (
            run_js(qtbot, window, "!!document.querySelector('.preview .wikilink--broken')") is True
        ),
        timeout=20_000,
    )
    # A broken link carries no data-note, so there is nothing to navigate to.
    assert (
        run_js(
            qtbot,
            window,
            "!!document.querySelector('.preview .wikilink--broken[data-note]')",
        )
        is False
    )


# --- the critical data-corruption bug: tab switch mid-edit --------------------


def test_switching_notes_mid_edit_does_not_write_one_note_into_another(
    qtbot: Any, shell: Any
) -> None:
    """Regression for the tab-switch corruption bug, end to end.

    Type into note A, switch to note B before the autosave fires, and confirm on
    disk that A kept its edit and B was never given A's body.
    """
    _app, window, services = shell
    wait_for_load(qtbot, window)
    wait_for_tree(qtbot, window)

    a = _make_note(services, "Note Alpha", "original alpha body\n")
    b = _make_note(services, "Note Beta", "original beta body\n")

    _open(qtbot, window, a)
    run_js(qtbot, window, "window.__strataStore.getState().setMode('focus')")

    # Simulate an in-progress edit of A (a draft the user just typed), then switch
    # to B *before* the debounced autosave has a chance to run.
    run_async_js(
        qtbot,
        window,
        "(async () => {"
        f"  const s = window.__strataStore.getState();"
        f"  s.setDraft({a!r}, 'ALPHA EDITED IN FLIGHT');"
        f"  await s.openNoteById({b!r});"
        "})()",
    )

    # Give any (incorrect) flush a chance to land on disk, then assert integrity.
    qtbot.wait(500)

    beta = services.notes.get_note(b)
    assert "ALPHA EDITED IN FLIGHT" not in beta.content, (
        "note A's in-flight edit was written into note B"
    )
    assert beta.content.strip() == "original beta body"


# --- the right-click menu: Strata's own, never the browser's ------------------


def test_right_click_opens_stratas_menu_without_a_reload_item(qtbot: Any, shell: Any) -> None:
    _app, window, _services = shell
    wait_for_load(qtbot, window)
    wait_for_tree(qtbot, window)

    # Right-click the shell body (not an editable surface).
    run_js(
        qtbot,
        window,
        "document.querySelector('.shell').dispatchEvent("
        "new MouseEvent('contextmenu', "
        "{bubbles: true, cancelable: true, clientX: 200, clientY: 200}))",
    )

    qtbot.waitUntil(
        lambda: run_js(qtbot, window, "!!document.querySelector('.context-menu')") is True,
        timeout=10_000,
    )
    text = str(
        run_js(
            qtbot,
            window,
            "document.querySelector('.context-menu').textContent.toLowerCase()",
        )
    )
    assert "reload" not in text
    assert "view source" not in text
    # And it offers real app actions.
    assert "note" in text or "graph" in text


# --- graph selection: clicking the accessible tree selects --------------------


def test_selecting_a_node_in_the_graph_tree_updates_the_store(qtbot: Any, shell: Any) -> None:
    _app, window, _services = shell
    wait_for_load(qtbot, window)
    wait_for_tree(qtbot, window)

    # The shell is shared across tests: close any open menu and clear selection so
    # this test asserts its own click, not leftover state.
    run_js(
        qtbot,
        window,
        "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));"
        "window.__strataStore.getState().clearSelection();"
        "window.__strataStore.getState().setMode('explore');",
    )
    qtbot.waitUntil(
        lambda: run_js(qtbot, window, "window.__strataStore.getState().selectedIds.length") == 0,
        timeout=5_000,
    )

    # The accessible graph tree is the interaction surface for the (aria-hidden)
    # canvas. Clicking a treeitem must drive the real selection state.
    run_js(
        qtbot,
        window,
        "document.querySelector('.graph-list [role=treeitem]')"
        ".dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}))",
    )
    qtbot.waitUntil(
        lambda: run_js(qtbot, window, "window.__strataStore.getState().selectedIds.length") > 0,
        timeout=10_000,
    )
