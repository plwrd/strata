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

**The overlay is not only for video.** A ``<video>`` is the loudest case, but
Chromium presents its *whole* composited output through a DirectComposition
swap chain, and on Windows 11 DWM will promote that swap chain to a hardware
scan-out plane (a "flip model" / MPO present) for an ordinary page — a scroll,
a CSS animation, a canvas, or nothing at all on some drivers. When it does, the
**entire window** is on a plane beside DWM's composition: it flickers as the
plane is engaged and released, and a capture taken mid-flicker gets the real
contents of a window whose affinity says "excluded". This is the intermittent
"leaks sometimes, on some event" that per-window affinity, popup closing, and
video-only flags all leave untouched, because the frame never reaches the
compositor the affinity governs. ``--disable-direct-composition`` takes the
whole engine off that path: every frame is presented into a surface DWM
composes, so the affinity covers all of it and there is no plane to flip.

These flags were previously set for the two embedded engines and **not** for the
launched Chrome — which is the backend a user picks precisely *because* it plays
video. Keeping them in one module is what stops the next engine from being
added without them.

The cost is honest: composited video is a little more GPU, software decode is
real CPU on playback, and disabling DirectComposition gives up the zero-copy
present path (a little more GPU for the whole window). All three are only paid
while the user has actually asked to be hidden — the point at which a flicker
that leaks is worse than a few percent of GPU.
"""

from __future__ import annotations

# Always on. An overlay plane also causes the flicker, which is a bug even for
# someone who is not hiding anything. This one disables *video* overlays
# specifically; the whole-compositor switch below is gated on hiding because it
# costs more.
CAPTURE_SAFE_ARGUMENTS: tuple[str, ...] = ("--disable-direct-composition-video-overlays",)

# Only while "hidden for sharing" is on.
#
# Software decode: a hardware-decoded frame can still be handed to a zero-copy
# presentation path that never reaches DWM. Software decode keeps every frame in
# a surface DWM composes.
SOFTWARE_DECODE_ARGUMENT = "--disable-accelerated-video-decode"

# Disable DirectComposition entirely: this is the one that stops the *whole
# window* being promoted to a hardware plane (see the module docstring). Without
# it, an ordinary page — no video at all — can put the excluded window on a
# flip-model present that DWM scans out beside its composition, which flickers
# and leaks. Superset of the video-overlay flag; both are passed so the always-on
# guarantee holds even if this one is ever reverted.
DISABLE_DIRECT_COMPOSITION_ARGUMENT = "--disable-direct-composition"


def capture_flags(*, hiding: bool) -> tuple[str, ...]:
    """The flags an engine needs so its frames stay inside the exclusion."""
    if hiding:
        return (
            *CAPTURE_SAFE_ARGUMENTS,
            DISABLE_DIRECT_COMPOSITION_ARGUMENT,
            SOFTWARE_DECODE_ARGUMENT,
        )
    return CAPTURE_SAFE_ARGUMENTS
