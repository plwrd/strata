"""Path traversal and untrusted input.

The bridge never accepts a path, but note titles, folder names and imported
frontmatter all end up influencing the filesystem. These are the guards.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.errors import InvalidRequestError
from app.infrastructure.storage.markdown_store import parse_frontmatter
from app.infrastructure.storage.paths import resolve_within, safe_filename

pytestmark = pytest.mark.security


@pytest.mark.parametrize(
    "parts",
    [
        ("..", "etc", "passwd"),
        ("notes", "..", "..", "secrets.md"),
        ("C:\\Windows\\System32\\config",),
        ("/etc/shadow",),
        ("a/../../b",),
    ],
)
def test_traversal_is_refused(tmp_path: Path, parts: tuple[str, ...]) -> None:
    with pytest.raises(InvalidRequestError):
        resolve_within(tmp_path, *parts)


def test_a_legitimate_path_resolves(tmp_path: Path) -> None:
    resolved = resolve_within(tmp_path, "Security", "Threat Model.md")
    assert resolved == (tmp_path / "Security" / "Threat Model.md").resolve()


@pytest.mark.parametrize(
    ("raw", "expected_absent"),
    [
        ("../../evil", ".."),
        ("note:with*bad?chars", ":"),
        ("trailing dots...", "..."),
    ],
)
def test_filenames_are_sanitised(raw: str, expected_absent: str) -> None:
    assert expected_absent not in safe_filename(raw)


def test_reserved_windows_names_are_escaped() -> None:
    assert safe_filename("CON") != "CON"
    assert safe_filename("aux.md").startswith("_")


def test_empty_name_is_refused() -> None:
    with pytest.raises(InvalidRequestError):
        safe_filename("   ...  ")


def test_frontmatter_uses_safe_load_only() -> None:
    """A YAML tag that would construct a Python object must not be honoured."""
    hostile = "---\ndanger: !!python/object/apply:os.system ['echo pwned']\n---\n\nBody.\n"

    frontmatter, body = parse_frontmatter(hostile)

    # safe_load refuses the tag, so the document degrades to "no frontmatter"
    # rather than executing anything.
    assert frontmatter == {}
    assert "Body." in body


def test_malformed_frontmatter_does_not_break_the_note() -> None:
    frontmatter, body = parse_frontmatter("---\nthis: is: not: yaml:\n---\n\nStill readable.\n")

    assert frontmatter == {}
    assert "Still readable." in body


def test_non_mapping_frontmatter_is_discarded() -> None:
    frontmatter, _ = parse_frontmatter("---\n- just\n- a\n- list\n---\n\nBody\n")
    assert frontmatter == {}
