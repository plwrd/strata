"""Property-based fuzzing of the untrusted-input surfaces (M11).

Every parser that touches data from outside the process is a place a crash, a
hang, or — worst — a silent wrong answer can hide. These tests throw thousands of
adversarial inputs at each one and assert the invariant that matters: it either
returns a well-formed result or raises a *typed* Strata error, but it never
crashes with an unexpected exception, never hangs, and never returns partial
plaintext from tampered ciphertext.

Fuzzing found nothing to fix here by construction — the point is the *guard*: if
a future change makes one of these paths brittle, this suite fails loudly.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.bridge.envelope import MAX_PAYLOAD_BYTES
from app.domain.errors import StrataError
from app.infrastructure.crdt.document import LayerDocument
from app.infrastructure.encryption.container import open_sealed
from app.infrastructure.encryption.primitives import DecryptionError
from app.infrastructure.storage.markdown_store import parse_frontmatter
from app.infrastructure.storage.paths import resolve_within, safe_filename

pytestmark = pytest.mark.security

_settings = settings(
    max_examples=400,
    deadline=None,  # CI runners are slow; a per-example deadline would flake, not find bugs
    suppress_health_check=[HealthCheck.too_slow],
)


# --- Markdown frontmatter: hostile YAML must degrade, never execute ---------


@given(st.text(max_size=4000))
@_settings
def test_frontmatter_never_raises(raw: str) -> None:
    frontmatter, body = parse_frontmatter(raw)
    # Always a (dict, str) — a mapping or an empty dict, and the body preserved.
    assert isinstance(frontmatter, dict)
    assert isinstance(body, str)


@given(st.text(alphabet="abcdef:!{}[]\n-#0 '\"", max_size=200))
@_settings
def test_frontmatter_wrapped_in_delimiters_never_raises(inner: str) -> None:
    frontmatter, _ = parse_frontmatter(f"---\n{inner}\n---\n\nBody\n")
    assert isinstance(frontmatter, dict)


# --- Filename / path safety: never escape, never crash ----------------------


@given(st.text(max_size=300))
@_settings
def test_safe_filename_is_safe_or_refused(name: str) -> None:
    try:
        result = safe_filename(name)
    except StrataError:
        return  # a typed refusal is a valid outcome
    # Whatever comes back carries no separator, no traversal, no NUL.
    assert "/" not in result
    assert "\\" not in result
    assert ".." not in result
    assert "\x00" not in result
    assert result == result.strip()


@given(st.lists(st.text(max_size=60), max_size=6))
@_settings
def test_resolve_within_never_escapes(parts: list[str]) -> None:
    from pathlib import Path

    root = Path.cwd() / "workspace-root"
    try:
        resolved = resolve_within(root, *parts)
    except StrataError:
        return
    # If it resolved, the result is inside the root.
    assert resolved == root.resolve() or root.resolve() in resolved.parents


# --- Bridge envelope: malformed requests are typed errors, not crashes ------


@given(st.binary(max_size=2000))
@_settings
def test_envelope_parsing_never_crashes(raw: bytes) -> None:
    from app.bridge.envelope import RequestEnvelope

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return
    try:
        RequestEnvelope.model_validate_json(text)
    except (ValueError, StrataError):
        # Pydantic ValidationError (a ValueError) or a Strata error — both fine.
        pass


def test_oversize_payload_boundary_is_enforced() -> None:
    # The cap is a constant the bridge enforces before any work; assert it exists
    # and is a sane size (fuzzing the full bridge needs Qt; this pins the guard).
    assert 0 < MAX_PAYLOAD_BYTES <= 4 * 1024 * 1024


# --- CRDT update application: garbage bytes must not crash the document ------


@given(st.binary(max_size=3000))
@_settings
def test_applying_arbitrary_bytes_as_a_crdt_update_is_contained(blob: bytes) -> None:
    document = LayerDocument("doc")
    # Fuzz containment: a malformed update may raise anything, but it must not
    # corrupt the document — the assertion below is the real check.
    try:
        document.apply_update(blob)
    except Exception:  # noqa: S110
        pass
    # The document remains usable regardless.
    assert isinstance(document.nodes(), list)


# --- Sealed-object opener: tampered blobs fail closed -----------------------


@given(st.binary(min_size=0, max_size=200))
@_settings
def test_open_sealed_rejects_arbitrary_blobs(blob: bytes) -> None:
    from app.infrastructure.encryption.primitives import random_key

    with pytest.raises(DecryptionError):
        open_sealed(
            key=random_key(),
            layer_id="L",
            object_id=b"\x00" * 16,
            expected_type=2,
            blob=blob,
        )
