"""The Chromium flags that keep a frame inside "Hidden for sharing".

One list, three engines. Qt WebEngine (the app UI and the Qt research pane),
WebView2 (the Edge research pane) and the Chrome backend are all Chromium, and
all three are subject to the same rule:

    ``SetWindowDisplayAffinity`` is enforced by DWM, so it can only exclude what
    DWM composes.

When Chromium promotes a ``<video>`` to a DirectComposition **hardware overlay**,
that plane is scanned out beside DWM's output rather than through it. The video
then appears in a recording of a window that is otherwise excluded — and the
window flickers as the overlay is taken and released. One cause, two symptoms,
and no amount of per-window affinity fixes either: the frame never passes
through the compositor the affinity governs.

These flags were previously set for the two embedded engines and **not** for the
launched Chrome — which is the backend a user picks precisely *because* it plays
video. Keeping them in one module is what stops the next engine from being
added without them.

The cost is honest: composited video is a little more GPU, and software decode
is real CPU on playback. The decode flag is therefore only paid while the user
has actually asked to be hidden.
"""

from __future__ import annotations

# Always on. An overlay plane also causes the flicker, which is a bug even for
# someone who is not hiding anything.
CAPTURE_SAFE_ARGUMENTS: tuple[str, ...] = ("--disable-direct-composition-video-overlays",)

# Only while "hidden for sharing" is on: a hardware-decoded frame can still be
# handed to a zero-copy presentation path that never reaches DWM. Software
# decode keeps every frame in a surface DWM composes.
SOFTWARE_DECODE_ARGUMENT = "--disable-accelerated-video-decode"


def capture_flags(*, hiding: bool) -> tuple[str, ...]:
    """The flags an engine needs so its frames stay inside the exclusion."""
    if hiding:
        return (*CAPTURE_SAFE_ARGUMENTS, SOFTWARE_DECODE_ARGUMENT)
    return CAPTURE_SAFE_ARGUMENTS
