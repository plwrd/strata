"""The prompt library: saved, versioned, counted AI instructions.

Storage is ``.strata/ai/prompts.jsonl`` — append-only; the latest record per id
wins, so the file itself is the version history. Deleting appends a tombstone
(a record with ``deleted`` marker via empty prompt is not allowed by the model,
so tombstones are separate lines) — kept simple: deletion rewrites the file
without the id, atomically. Prompt text is user-authored instruction content;
it is rendered into the request's instruction channel only, never into the
untrusted sources block.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from app.domain.errors import InvalidRequestError, NotFoundError
from app.domain.ids import new_object_id
from app.domain.prompts import PromptCategory, SavedPrompt
from app.infrastructure.logging.logger import get_logger
from app.infrastructure.storage.markdown_store import now_iso
from app.infrastructure.storage.paths import replace_atomic
from app.services.workspace_service import WorkspaceService

logger = get_logger(__name__)

PROMPTS_FILE = "prompts.jsonl"
MAX_PROMPTS = 500


class PromptLibraryService:
    def __init__(self, workspace: WorkspaceService) -> None:
        self._workspace = workspace

    def _path(self) -> Path | None:
        if not self._workspace.is_open:
            return None
        return self._workspace.root / ".strata" / "ai" / PROMPTS_FILE

    # -- reading -------------------------------------------------------------

    def _load_all(self) -> dict[str, SavedPrompt]:
        """Latest record per id wins; corrupt lines are skipped."""
        path = self._path()
        if path is None or not path.is_file():
            return {}
        latest: dict[str, SavedPrompt] = {}
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return {}
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = SavedPrompt.model_validate_json(line)
            except ValidationError:
                continue
            latest[record.id] = record
        return latest

    def list_prompts(self) -> list[SavedPrompt]:
        """Most recently updated first."""
        return sorted(self._load_all().values(), key=lambda prompt: prompt.updated_at, reverse=True)

    def get(self, prompt_id: str) -> SavedPrompt:
        prompt = self._load_all().get(prompt_id)
        if prompt is None:
            raise NotFoundError("That saved prompt no longer exists.")
        return prompt

    def history(self, prompt_id: str) -> list[SavedPrompt]:
        """Every stored version of one prompt, oldest first."""
        path = self._path()
        if path is None or not path.is_file():
            return []
        versions: list[SavedPrompt] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = SavedPrompt.model_validate_json(line)
            except ValidationError:
                continue
            if record.id == prompt_id:
                versions.append(record)
        return versions

    # -- writing -------------------------------------------------------------

    def save(
        self,
        *,
        prompt_id: str = "",
        name: str,
        prompt_text: str,
        description: str = "",
        category: PromptCategory = "other",
        model_preference: str = "",
        temperature: float | None = None,
    ) -> SavedPrompt:
        """Create, or save a new version over an existing id."""
        path = self._path()
        if path is None:
            raise InvalidRequestError("Open a workspace to save prompts.")
        existing = self._load_all()
        if not prompt_id and len(existing) >= MAX_PROMPTS:
            raise InvalidRequestError("The prompt library is full.")

        now = now_iso()
        previous = existing.get(prompt_id) if prompt_id else None
        record = SavedPrompt(
            id=prompt_id or f"prompt_{new_object_id()[:16]}",
            name=name,
            description=description,
            category=category,
            prompt_text=prompt_text,
            model_preference=model_preference,
            temperature=temperature,
            version=(previous.version + 1) if previous else 1,
            usage_count=previous.usage_count if previous else 0,
            created_at=previous.created_at if previous else now,
            updated_at=now,
            last_used_at=previous.last_used_at if previous else "",
        )
        self._append(record)
        logger.info("prompts.saved", version=record.version)
        return record

    def record_use(self, prompt_id: str) -> SavedPrompt:
        """Bump the usage counter and return the prompt to run."""
        prompt = self.get(prompt_id)
        used = prompt.model_copy(
            update={"usage_count": prompt.usage_count + 1, "last_used_at": now_iso()}
        )
        self._append(used)
        return used

    def delete(self, prompt_id: str) -> None:
        """Remove a prompt and its version trail (atomic rewrite)."""
        path = self._path()
        if path is None or not path.is_file():
            raise NotFoundError("That saved prompt no longer exists.")
        if prompt_id not in self._load_all():
            raise NotFoundError("That saved prompt no longer exists.")
        kept: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                if json.loads(stripped).get("id") == prompt_id:
                    continue
            except json.JSONDecodeError:
                continue
            kept.append(stripped)
        temporary = path.with_suffix(".jsonl.tmp")
        temporary.write_text(
            "\n".join(kept) + ("\n" if kept else ""), encoding="utf-8", newline="\n"
        )
        replace_atomic(temporary, path)
        logger.info("prompts.deleted")

    def _append(self, record: SavedPrompt) -> None:
        path = self._path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(record.model_dump_json() + "\n")
        except OSError:
            logger.warning("prompts.append_failed")
