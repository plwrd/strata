"""Persistent AI memory: receipts, execution records, the applied-plan audit log.

Storage is append-only JSONL under ``<workspace>/.strata/ai/`` — one complete
record per line, readable with any text editor, deletable by the user, included
in workspace backups. In line with the rest of `.strata/`, nothing here is
hidden state: it is the workspace's own memory of what the AI did in it.

Two rules this service owns so that no caller can forget them:

1. **Redaction happens at write time.** A record whose execution involved a
   private layer is stripped of every content-bearing field before it reaches
   disk (:func:`app.domain.history.redact_execution`). The in-memory session
   copy stays complete; only the durable copy is redacted.
2. **History is never load-bearing.** A corrupt line loses one record, not the
   file; a missing directory means empty history, not an error. AI requests and
   plan application must succeed even if the history file is unwritable.
"""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.domain.export import PrivacyReceipt
from app.domain.history import AIExecutionRecord, redact_applied_plan, redact_execution
from app.domain.operations import AppliedPlan
from app.infrastructure.logging.logger import get_logger
from app.infrastructure.storage.paths import replace_atomic
from app.services.workspace_service import WorkspaceService

logger = get_logger(__name__)

RECEIPTS_FILE = "receipts.jsonl"
EXECUTIONS_FILE = "executions.jsonl"
APPLIED_PLANS_FILE = "applied_plans.jsonl"

# Loaded records per file. Older lines beyond the cap are dropped at read time
# and physically removed when a file grows past the compaction threshold.
MAX_RECORDS = 1000
_COMPACT_BYTES = 8 * 1024 * 1024

# Persisted content caps. The bridge already caps prompts at 32k characters;
# responses are capped here so a runaway stream cannot balloon the history file.
_PROMPT_CAP = 32_000
_RESPONSE_CAP = 200_000

_RecordT = TypeVar("_RecordT", bound=BaseModel)


class AIHistoryService:
    """Reads and writes the per-workspace AI history files."""

    def __init__(self, workspace: WorkspaceService) -> None:
        self._workspace = workspace

    # -- location ------------------------------------------------------------

    def _root(self) -> Path | None:
        """The history directory of the open workspace, or None when closed.

        Resolved on every call rather than cached: the open workspace can
        change, and history must always land in the workspace it belongs to.
        """
        if not self._workspace.is_open:
            return None
        return self._workspace.root / ".strata" / "ai"

    def _public_layer_ids(self) -> set[str]:
        if not self._workspace.is_open:
            return set()
        return {
            layer.id for layer in self._workspace.descriptor.layers if layer.visibility == "public"
        }

    # -- receipts ------------------------------------------------------------

    def record_receipt(self, receipt: PrivacyReceipt) -> None:
        """Receipts are metadata by construction — persisted verbatim."""
        self._append(RECEIPTS_FILE, receipt.model_dump_json())

    def list_receipts(self) -> list[PrivacyReceipt]:
        """Newest first."""
        return list(reversed(self._load(RECEIPTS_FILE, PrivacyReceipt)))

    # -- executions ----------------------------------------------------------

    def record_execution(self, record: AIExecutionRecord) -> AIExecutionRecord:
        """Apply the redaction rule and persist. Returns what was written.

        The rule is conservative: a layer id that is not a *known public* layer
        counts as private, so an unknown or stale id can never smuggle content
        to disk.
        """
        public = self._public_layer_ids()
        involves_private = record.private_source_count > 0 or any(
            layer_id not in public for layer_id in record.layer_ids
        )
        if involves_private:
            stored = redact_execution(record)
        else:
            stored = record.model_copy(
                update={
                    "prompt": record.prompt[:_PROMPT_CAP],
                    "response_text": record.response_text[:_RESPONSE_CAP],
                }
            )
        self._append(EXECUTIONS_FILE, stored.model_dump_json())
        return stored

    def list_executions(self, limit: int = 100) -> list[AIExecutionRecord]:
        """Newest first, at most ``limit`` records."""
        records = self._load(EXECUTIONS_FILE, AIExecutionRecord)
        return list(reversed(records))[: max(0, limit)]

    def get_execution(self, execution_id: str) -> AIExecutionRecord | None:
        for record in self._load(EXECUTIONS_FILE, AIExecutionRecord):
            if record.id == execution_id:
                return record
        return None

    # -- applied plans -------------------------------------------------------

    def record_applied_plan(self, plan: AppliedPlan, *, involves_private: bool) -> None:
        stored = redact_applied_plan(plan) if involves_private else plan
        self._append(APPLIED_PLANS_FILE, stored.model_dump_json())

    def list_applied_plans(self) -> list[AppliedPlan]:
        """Newest first."""
        return list(reversed(self._load(APPLIED_PLANS_FILE, AppliedPlan)))

    def mark_plan_undone(self, plan_id: str) -> None:
        """Record that a persisted plan was undone (atomic rewrite)."""
        plans = self._load(APPLIED_PLANS_FILE, AppliedPlan)
        changed = False
        for plan in plans:
            if plan.plan_id == plan_id and not plan.undone:
                plan.undone = True
                changed = True
        if changed:
            self._rewrite(APPLIED_PLANS_FILE, [plan.model_dump_json() for plan in plans])

    # -- privacy control -----------------------------------------------------

    def clear(self) -> int:
        """Delete all AI history files. The user's "forget my AI activity" control.

        Returns the number of files removed. Layer content is untouchable from
        here by construction — this directory only ever holds history.
        """
        root = self._root()
        if root is None:
            return 0
        removed = 0
        for filename in (RECEIPTS_FILE, EXECUTIONS_FILE, APPLIED_PLANS_FILE):
            path = root / filename
            try:
                if path.is_file():
                    path.unlink()
                    removed += 1
            except OSError:
                logger.warning("ai_history.clear_failed", file=filename)
        logger.info("ai_history.cleared", files=removed)
        return removed

    # -- storage -------------------------------------------------------------

    def _append(self, filename: str, line: str) -> None:
        root = self._root()
        if root is None:
            return
        path = root / filename
        try:
            root.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
            if path.stat().st_size > _COMPACT_BYTES:
                self._compact(path)
        except OSError:
            # History must never break the request it is recording.
            logger.warning("ai_history.append_failed", file=filename)

    def _compact(self, path: Path) -> None:
        lines = path.read_text(encoding="utf-8").splitlines()
        self._rewrite(path.name, [line for line in lines if line.strip()][-MAX_RECORDS:])

    def _rewrite(self, filename: str, lines: list[str]) -> None:
        root = self._root()
        if root is None:
            return
        try:
            root.mkdir(parents=True, exist_ok=True)
            temporary = root / (filename + ".tmp")
            temporary.write_text(
                "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8", newline="\n"
            )
            replace_atomic(temporary, root / filename)
        except OSError:
            logger.warning("ai_history.rewrite_failed", file=filename)

    def _load(self, filename: str, model_type: type[_RecordT]) -> list[_RecordT]:
        """Oldest first. Corrupt lines are skipped — one bad record never costs
        the history."""
        root = self._root()
        if root is None:
            return []
        path = root / filename
        if not path.is_file():
            return []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            logger.warning("ai_history.read_failed", file=filename)
            return []
        records: list[_RecordT] = []
        for line in lines[-MAX_RECORDS:]:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(model_type.model_validate_json(line))
            except ValidationError:
                continue
        return records
