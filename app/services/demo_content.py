"""Seed content for a brand-new workspace.

These are real Markdown files written to disk through the normal store, not
fixtures and not mocks: the app reads them back through the same code path as
any user note. A new workspace that is completely empty gives the graph, the
selection model and the export pipeline nothing to show, so a first-run
workspace starts with a small, deletable set of notes about Strata itself.
"""

from __future__ import annotations

from typing import Final

from app.infrastructure.storage.markdown_store import MarkdownLayerStore

_SeedNote = tuple[str, str, dict[str, object], str]

DEMO_NOTES: Final[list[_SeedNote]] = [
    (
        "Architecture",
        "Strata Overview",
        {"type": "decision", "status": "active", "tags": ["strata", "overview"]},
        """Strata is a local-first knowledge workspace: Markdown files you own, layered by
sensitivity, connected by a graph, and readable by an AI model only when you say so.

The product loop is capture, organise, connect, explore, select, ask, review, apply.

supports:: [[Encryption Architecture]]
expands:: [[Knowledge Graph]]

See also [[AI Context Composer]] and [[Layers and Lenses]].
""",
    ),
    (
        "Architecture",
        "Layers and Lenses",
        {"type": "architecture-component", "tags": ["layers"]},
        """A **layer** is a content, permission, encryption, synchronisation and AI-access
boundary. Every knowledge object belongs to exactly one layer.

A **Knowledge Lens** is a saved perspective over one or more layers. A lens is a view
decision; unlocking a private layer is a security decision. A lens can never grant access.

depends_on:: [[Strata Overview]]
references:: [[Encryption Architecture]]
""",
    ),
    (
        "Security",
        "Encryption Architecture",
        {"type": "architecture-component", "status": "designed", "tags": ["encryption", "security"]},
        """Every private layer holds a random 256-bit layer data key. The password never *is*
the key: Argon2id derives a key-encryption key that wraps the layer key.

Objects are encrypted independently with XChaCha20-Poly1305 and 24-byte random nonces.
Filenames are random opaque ids, so the disk reveals no titles, no folders and no tags.

depends_on:: [[Threat Model]]
supports:: [[Strata Overview]]

The honest limits are recorded in [[Threat Model]] — object count and bucketed object
sizes still leak.
""",
    ),
    (
        "Security",
        "Threat Model",
        {"type": "security-threat", "status": "living", "tags": ["security"]},
        """We defend against a lost device, a malicious collaborator, a compromised relay and a
hostile note author (prompt injection).

We do not defend against malware running on an unlocked machine, and we do not claim
"zero knowledge".

contradicts:: [[Marketing Claims]]
evidence_for:: [[Encryption Architecture]]
""",
    ),
    (
        "Security",
        "Marketing Claims",
        {"type": "decision", "status": "rejected", "tags": ["security"]},
        """Rejected: describing Strata as "military-grade" or "zero knowledge".

Neither claim survives contact with the threat model, and both erode trust when a user
reads the leakage section. This note exists so the graph has a real contradiction edge.

contradicts:: [[Threat Model]]
""",
    ),
    (
        "Graph",
        "Knowledge Graph",
        {"type": "architecture-component", "tags": ["graph", "3d"]},
        """The graph is a product surface, not decoration. Nodes are notes, folders, tags and
concepts; edges are links, typed relationships, folder membership and semantic similarity.

Selection in the graph *is* the AI context: what you illuminate is what the model sees.

supports:: [[AI Context Composer]]
derived_from:: [[Strata Overview]]
""",
    ),
    (
        "AI",
        "AI Context Composer",
        {"type": "architecture-component", "tags": ["ai", "export"]},
        """Select nodes anywhere — graph, tree, search, table — write a prompt, choose a provider,
review exactly what will be sent, then export Markdown or send the request.

Nothing is ever sent silently. Locked layers are never included. Every remote request and
every decrypted export writes a privacy receipt.

depends_on:: [[Knowledge Graph]]
references:: [[Provider Neutrality]]
""",
    ),
    (
        "AI",
        "Provider Neutrality",
        {"type": "decision", "status": "active", "tags": ["ai", "providers"]},
        """One `AIProvider` protocol, many adapters: OpenAI, Anthropic, Ollama, llama.cpp, LM
Studio, any OpenAI-compatible endpoint, and the Claude CLI as a local *process* adapter.

The Claude CLI is not an offline model. It sends data to Anthropic and is labelled as
remote everywhere in the UI.

supports:: [[AI Context Composer]]
""",
    ),
]


def seed_demo_workspace(store: MarkdownLayerStore) -> int:
    """Write the starter notes into a layer. Returns the number of notes written."""
    written = 0
    for folder, title, properties, body in DEMO_NOTES:
        store.write_note(
            folder_path=folder,
            title=title,
            content=f"# {title}\n\n{body.strip()}\n",
            properties=properties,
        )
        written += 1
    return written
