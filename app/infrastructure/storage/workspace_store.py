"""Workspace persistence: ``workspace.json`` plus one directory per layer.

Layout (see docs/architecture/storage-layout.md)::

    MyWorkspace/
      workspace.json            # descriptor: layers, order, lenses
      layers/
        layer_ab12cd34/         # public layer: plain Markdown, source of truth
          Architecture/Encryption Architecture.md
        layer_ef56ab78/         # private layer (Milestone 3): opaque objects only
          layer.header
          objects/02/02f8a7...
      .strata/
        logs/
        trash/
        snapshots/
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from app.domain.errors import InvalidRequestError, NotFoundError
from app.domain.workspace import WORKSPACE_FORMAT_VERSION, WorkspaceDescriptor
from app.infrastructure.storage.paths import resolve_within

WORKSPACE_FILE = "workspace.json"
LAYERS_DIR = "layers"
INTERNAL_DIR = ".strata"


class WorkspaceStore:
    """Reads and writes the workspace descriptor. Knows nothing about content."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @property
    def descriptor_path(self) -> Path:
        return self.root / WORKSPACE_FILE

    @property
    def layers_root(self) -> Path:
        return self.root / LAYERS_DIR

    @property
    def internal_root(self) -> Path:
        return self.root / INTERNAL_DIR

    def exists(self) -> bool:
        return self.descriptor_path.is_file()

    def layer_root(self, layer_id: str) -> Path:
        return resolve_within(self.layers_root, layer_id)

    def initialise(self, descriptor: WorkspaceDescriptor) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.layers_root.mkdir(parents=True, exist_ok=True)
        for sub in ("logs", "trash", "snapshots", "exports"):
            (self.internal_root / sub).mkdir(parents=True, exist_ok=True)
        self.save(descriptor)

    def load(self) -> WorkspaceDescriptor:
        if not self.exists():
            raise NotFoundError("No workspace was found at this location.")
        try:
            raw = json.loads(self.descriptor_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise InvalidRequestError("The workspace file could not be read.") from exc
        if not isinstance(raw, dict):
            raise InvalidRequestError("The workspace file is not valid.")
        version = raw.get("format_version", 0)
        if version > WORKSPACE_FORMAT_VERSION:
            raise InvalidRequestError(
                "This workspace was created by a newer version of Strata.",
                details={
                    "found": version,
                    "supported": WORKSPACE_FORMAT_VERSION,
                },
            )
        try:
            return WorkspaceDescriptor.model_validate(raw)
        except ValidationError as exc:
            raise InvalidRequestError("The workspace file is not valid.") from exc

    def save(self, descriptor: WorkspaceDescriptor) -> None:
        """Atomic write: a crash mid-save must never destroy the descriptor."""
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.descriptor_path.with_suffix(".json.tmp")
        temporary.write_text(
            descriptor.model_dump_json(indent=2),
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(self.descriptor_path)
