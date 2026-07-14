"""The plaintext scanner must catch what it claims to catch — and must not cry wolf.

A detector that has never caught anything is indistinguishable from a broken one,
and a detector that fires on clean data is a detector nobody runs. Both directions
are tested here so that Milestone 3 inherits a scanner that is actually trusted.
"""

from __future__ import annotations

import secrets
from pathlib import Path

import pytest
from scripts.scan_plaintext import scan_layer, self_test

pytestmark = pytest.mark.security


def _clean_layer(root: Path, objects: int = 8) -> None:
    (root / "objects").mkdir(parents=True, exist_ok=True)
    (root / "layer.header").write_bytes(b'{"kdf":"argon2id","v":1}')
    for _ in range(objects):
        object_id = secrets.token_hex(16)
        shard = root / "objects" / object_id[:2]
        shard.mkdir(parents=True, exist_ok=True)
        # 71-byte readable header + random ciphertext, as the container specifies.
        (shard / object_id).write_bytes(b"\x01" * 71 + secrets.token_bytes(1024))


def test_the_self_test_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert self_test() == 0


def test_a_clean_layer_is_never_flagged(tmp_path: Path) -> None:
    """Guards against a false positive that would make the check untrustworthy.

    Random ciphertext contains two-byte sequences like ``# `` or ``[[`` by chance,
    so the structure signatures only apply to bodies that are actually text.
    """
    for _ in range(25):
        root = tmp_path / secrets.token_hex(4)
        _clean_layer(root)
        assert scan_layer(root, ["Northwind"]) == []


def test_a_plaintext_filename_is_caught(tmp_path: Path) -> None:
    root = tmp_path / "layer"
    _clean_layer(root)
    (root / "Acquisition Notes.md").write_text("# Secret\n", encoding="utf-8")

    problems = [finding.problem for finding in scan_layer(root, [])]

    assert any("unexpected entry" in problem for problem in problems)


def test_an_unencrypted_note_body_is_caught(tmp_path: Path) -> None:
    root = tmp_path / "layer"
    _clean_layer(root)

    object_id = secrets.token_hex(16)
    shard = root / "objects" / object_id[:2]
    shard.mkdir(parents=True, exist_ok=True)
    (shard / object_id).write_bytes(
        b"\x01" * 71 + b"---\ntitle: Acquisition Of Northwind\ntags: [deal]\n---\n\n"
        b"# Plan\n\nWe will offer 4.2 million for Northwind in Q3, which is a\n"
        b"sentence long enough for the entropy and printability checks to see.\n"
    )

    problems = [finding.problem for finding in scan_layer(root, ["Northwind"])]

    assert any("printable text" in problem for problem in problems)
    assert any("marker" in problem for problem in problems)
    assert any("entropy" in problem for problem in problems)
    assert any("frontmatter" in problem for problem in problems)


def test_a_deterministic_filename_is_caught(tmp_path: Path) -> None:
    """Deterministic filename encryption is banned: the id must be opaque."""
    root = tmp_path / "layer"
    _clean_layer(root)

    shard = root / "objects" / "ab"
    shard.mkdir(parents=True, exist_ok=True)
    (shard / "encrypted-Meeting-Notes").write_bytes(b"\x01" * 71 + secrets.token_bytes(256))

    problems = [finding.problem for finding in scan_layer(root, [])]

    assert any("opaque" in problem for problem in problems)
