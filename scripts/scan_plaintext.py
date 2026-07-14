"""Scan a private layer's storage for plaintext.

This is the tool that turns "the disk reveals nothing" from a claim into a
checked property. It walks a layer directory and fails if it finds:

* a readable filename or folder name (private layers must use opaque ids only);
* a known plaintext marker inside an object file;
* a file extension that discloses the object type;
* recognisable Markdown, YAML or JSON structure in an object body.

It exists *before* Milestone 3 on purpose: writing the detector after the
encryption is how you end up with a detector that agrees with whatever the
encryption happens to do.

Usage::

    python scripts/scan_plaintext.py <layer-dir> [--marker SECRET]...
    python scripts/scan_plaintext.py --self-test
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

# A private object id is 32 lowercase hex characters (16 random bytes), stored at
# objects/<first two chars>/<id>. Anything else in the tree is suspicious.
OBJECT_ID = re.compile(r"^[0-9a-f]{32}$")
SHARD = re.compile(r"^[0-9a-f]{2}$")

ALLOWED_TOP_LEVEL = {"layer.header", "objects"}

# Structure that must never survive encryption.
PLAINTEXT_SIGNATURES: tuple[tuple[str, bytes], ...] = (
    ("YAML frontmatter", b"---\n"),
    ("Markdown heading", b"# "),
    ("Markdown link", b"]("),
    ("wiki link", b"[["),
    ("JSON object", b'{"'),
    ("frontmatter key", b"tags:"),
    ("frontmatter key", b"title:"),
)

MIN_ENTROPY_SAMPLE = 64

# Short byte signatures like b"# " or b"[[" occur in random ciphertext by chance
# roughly 0.8% of the time per 512-byte object, which across seven signatures and
# thousands of objects means a scanner that cries wolf constantly — and a security
# check nobody trusts is a security check nobody runs. Structure only *means*
# anything when the body is text, so the signatures are gated on printability and
# entropy carries the load for genuinely random data.
PRINTABLE_RATIO_THRESHOLD = 0.70
MIN_ENTROPY_BITS = 7.0


class Finding:
    def __init__(self, path: Path, problem: str) -> None:
        self.path = path
        self.problem = problem

    def __str__(self) -> str:
        return f"  {self.path}: {self.problem}"


def shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = [0] * 256
    for byte in data:
        counts[byte] += 1
    total = len(data)
    import math

    return -sum((count / total) * math.log2(count / total) for count in counts if count)


def printable_ratio(data: bytes) -> float:
    """Fraction of bytes that are printable ASCII, tab, newline or carriage return."""
    if not data:
        return 0.0
    printable = sum(1 for byte in data if 32 <= byte <= 126 or byte in (9, 10, 13))
    return printable / len(data)


def is_texty(data: bytes) -> bool:
    return len(data) >= MIN_ENTROPY_SAMPLE and printable_ratio(data) > PRINTABLE_RATIO_THRESHOLD


def scan_layer(root: Path, markers: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    encoded_markers = [(marker, marker.encode("utf-8")) for marker in markers]

    if not root.is_dir():
        raise SystemExit(f"error: {root} is not a directory")

    for entry in root.iterdir():
        if entry.name not in ALLOWED_TOP_LEVEL:
            findings.append(Finding(entry, "unexpected entry in a private layer"))

    objects = root / "objects"
    if not objects.is_dir():
        return findings

    for shard in objects.iterdir():
        if not shard.is_dir() or not SHARD.match(shard.name):
            findings.append(Finding(shard, "not a valid two-character shard"))
            continue

        for obj in shard.iterdir():
            if obj.suffix:
                findings.append(
                    Finding(obj, f"file extension '{obj.suffix}' discloses the object type")
                )
            if not OBJECT_ID.match(obj.stem):
                findings.append(Finding(obj, "filename is not an opaque 32-hex object id"))
            if not obj.stem.startswith(shard.name):
                findings.append(Finding(obj, "object is in the wrong shard"))

            data = obj.read_bytes()

            for marker, encoded in encoded_markers:
                if encoded in data:
                    findings.append(Finding(obj, f"contains the plaintext marker {marker!r}"))

            # Skip the container header before looking for structure: the header is
            # supposed to be readable (version, algorithm, nonce), the body is not.
            body = data[71:] if len(data) > 71 else b""

            if is_texty(body):
                findings.append(
                    Finding(
                        obj,
                        f"ciphertext body is {printable_ratio(body):.0%} printable text "
                        "and should be indistinguishable from random",
                    )
                )
                for name, signature in PLAINTEXT_SIGNATURES:
                    if signature in body:
                        findings.append(Finding(obj, f"ciphertext contains {name} ({signature!r})"))

            if len(body) >= MIN_ENTROPY_SAMPLE:
                entropy = shannon_entropy(body)
                if entropy < MIN_ENTROPY_BITS:
                    findings.append(
                        Finding(obj, f"ciphertext entropy is only {entropy:.2f} bits/byte")
                    )

    return findings


def self_test() -> int:
    """Prove the scanner detects what it claims to detect.

    A scanner that has never caught anything is indistinguishable from one that
    cannot. CI runs this until Milestone 3 provides a real layer to scan.
    """
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw) / "layer"
        (root / "objects" / "ab").mkdir(parents=True)

        # A layer that should pass: opaque id, high-entropy body.
        import secrets

        good_id = "ab" + secrets.token_hex(15)
        (root / "objects" / "ab" / good_id).write_bytes(b"S" * 71 + secrets.token_bytes(512))
        (root / "layer.header").write_bytes(b'{"kdf":"argon2id"}')

        clean = scan_layer(root, ["Northwind"])
        if clean:
            print("SELF-TEST FAILED: a clean layer was flagged:")
            for finding in clean:
                print(finding)
            return 1

        # Now plant every failure the scanner must catch.
        (root / "Meeting Notes.md").write_text("# Real filename\n", encoding="utf-8")
        bad_id = "ab" + secrets.token_hex(15)
        (root / "objects" / "ab" / f"{bad_id}.md").write_bytes(
            b"S" * 71 + b"---\ntitle: Acquisition Of Northwind\ntags: [deal]\n---\n\n"
            b"# Plan\n\nWe will offer 4.2 million in Q3. English prose has an entropy "
            b"of roughly 4 bits per byte, which is what the entropy check is for.\n"
        )

        dirty = scan_layer(root, ["Northwind"])
        problems = {finding.problem for finding in dirty}

        expected = [
            "unexpected entry",
            "extension",
            "marker",
            "frontmatter",
            "entropy",
        ]
        missing = [word for word in expected if not any(word in problem for problem in problems)]
        if missing:
            print(f"SELF-TEST FAILED: the scanner missed {missing}")
            for finding in dirty:
                print(finding)
            return 1

    print("self-test passed: the scanner detects plaintext filenames, extensions,")
    print("frontmatter, known markers and low-entropy bodies.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("layer", nargs="?", type=Path, help="the private layer directory")
    parser.add_argument(
        "--marker",
        action="append",
        default=[],
        help="a plaintext string that must not appear (repeatable)",
    )
    parser.add_argument("--self-test", action="store_true", help="verify the scanner itself")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    if args.layer is None:
        parser.error("a layer directory is required (or use --self-test)")

    findings = scan_layer(args.layer, args.marker)
    if findings:
        print(f"FAIL: {len(findings)} plaintext finding(s) in {args.layer}:")
        for finding in findings:
            print(finding)
        return 1

    print(f"OK: no plaintext found in {args.layer}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
