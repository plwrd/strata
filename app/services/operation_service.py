"""The transactional AI change engine.

The workflow the product mandates:

    prompt → AI plan → schema validation → security validation → visual diff →
    approval → transactional apply → audit record → undo or commit

This service owns validation, diff, apply and undo. Generating the plan (calling a
model) is the AI service's job; *executing* one safely is this one's.

**Transactional** means all-or-nothing with a recovery point: a snapshot is taken
before the first change, so if operation 7 of 10 fails, undo restores the snapshot
and the workspace is exactly as it was. AI-applied changes are marked in the audit
log, and every applied plan can be undone until it is explicitly committed.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.domain.errors import ConflictError, InvalidRequestError, NotFoundError
from app.domain.note import Note
from app.domain.operations import (
    AppliedPlan,
    DiffEntry,
    Operation,
    OperationPlan,
    OperationResult,
    PlanReview,
)
from app.infrastructure.logging.logger import get_logger
from app.services.note_service import NoteService
from app.services.snapshot_service import SnapshotService
from app.services.workspace_service import WorkspaceService

logger = get_logger(__name__)

# Operations that create a note, and therefore need a title.
_NEEDS_TITLE = frozenset({"create_note", "create_task"})
# Operations that target an existing note.
_NEEDS_NOTE = frozenset(
    {
        "update_note",
        "append_note",
        "move_note",
        "rename_note",
        "set_property",
        "add_tag",
        "remove_tag",
        "add_link",
        "add_relationship",
        "archive_note",
        "delete_note",
    }
)

CONTENT_PREVIEW = 240


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


class OperationService:
    def __init__(
        self,
        workspace: WorkspaceService,
        notes: NoteService,
        snapshots: SnapshotService,
    ) -> None:
        self._workspace = workspace
        self._notes = notes
        self._snapshots = snapshots
        self._applied: list[AppliedPlan] = []

    # -- validation and diff -------------------------------------------------

    def review(self, plan: OperationPlan, *, allowed_layer_ids: list[str]) -> PlanReview:
        """Validate a plan against the *allowed* layers and build the diff.

        ``allowed_layer_ids`` is the set the user put in scope. An operation naming
        any other layer is marked invalid — the model cannot reach a layer the user
        did not include, and cannot reach a locked one at all.
        """
        allowed = set(allowed_layer_ids)
        note_index = {note.metadata.id: note for note in self._notes.list_notes()}
        layer_names = {layer.id: layer.display_name for layer in self._workspace.descriptor.layers}
        private_layers = {
            layer.id for layer in self._workspace.descriptor.layers if layer.visibility == "private"
        }

        entries: list[DiffEntry] = []
        private_touched: set[str] = set()

        for index, operation in enumerate(plan.operations):
            valid, problem = self._validate(operation, allowed, note_index)
            before, after, title = self._describe(operation, note_index)

            if operation.layer_id in private_layers and valid:
                private_touched.add(layer_names.get(operation.layer_id, operation.layer_id))

            entries.append(
                DiffEntry(
                    index=index,
                    type=operation.type,
                    layer_id=operation.layer_id,
                    layer_name=layer_names.get(operation.layer_id, "Unknown"),
                    is_private=operation.layer_id in private_layers,
                    is_destructive=operation.is_destructive,
                    title=title,
                    summary=self._summary(operation),
                    rationale=operation.rationale,
                    before=before,
                    after=after,
                    valid=valid,
                    problem=problem,
                )
            )

        valid_count = sum(1 for entry in entries if entry.valid)
        warnings: list[str] = []
        if private_touched:
            warnings.append(
                "This plan changes private layers. Their content stays encrypted on disk; "
                "the changes apply to the decrypted view while unlocked."
            )
        if plan.destructive_count:
            warnings.append(
                f"{plan.destructive_count} operation(s) change or remove existing content. "
                "Review them carefully — a snapshot is taken first, so this is undoable."
            )

        return PlanReview(
            plan=plan,
            entries=entries,
            valid_count=valid_count,
            invalid_count=len(entries) - valid_count,
            destructive_count=plan.destructive_count,
            private_layers_touched=sorted(private_touched),
            warnings=warnings,
        )

    def _validate(
        self,
        operation: Operation,
        allowed: set[str],
        note_index: dict[str, Note],
    ) -> tuple[bool, str]:
        # 1. The layer must be one the user put in scope. This is the containment
        #    rule: the AI cannot reach outside the selection.
        if operation.layer_id not in allowed:
            return False, "targets a layer that is not in scope"

        # 2. The layer must be readable *now*. A locked layer cannot be a target —
        #    there is nothing to change, and pretending otherwise would be a lie.
        try:
            self._workspace.require_readable_layer(operation.layer_id)
        except Exception:
            return False, "targets a layer that is locked or missing"

        # 3. Type-specific shape.
        if operation.type in _NEEDS_TITLE and not operation.title.strip():
            return False, "needs a title"

        if operation.type in _NEEDS_NOTE:
            if not operation.note_id:
                return False, "needs a target note"
            if operation.note_id not in note_index:
                return False, "targets a note that does not exist"
            if note_index[operation.note_id].metadata.layer_id != operation.layer_id:
                return False, "targets a note in a different layer"

        if operation.type == "add_relationship" and not operation.relationship:
            return False, "needs a relationship type"

        if operation.type in ("add_link", "add_relationship") and not (
            operation.target_note_id or operation.target_title
        ):
            return False, "needs a link target"

        if operation.type == "set_property" and not operation.property_key:
            return False, "needs a property name"

        return True, ""

    def _describe(self, operation: Operation, note_index: dict[str, Note]) -> tuple[str, str, str]:
        """Return (before, after, title) for the diff line."""
        note = note_index.get(operation.note_id or "")
        current_title = note.metadata.title if note else ""

        if operation.type == "create_note":
            return "", _preview(operation.content), operation.title
        if operation.type == "create_folder":
            return "", operation.folder_path, operation.folder_path
        if operation.type == "update_note":
            return (
                _preview(note.content if note else ""),
                _preview(operation.content),
                current_title,
            )
        if operation.type == "append_note":
            return (
                _preview(note.content if note else ""),
                "+ " + _preview(operation.content),
                current_title,
            )
        if operation.type == "rename_note":
            return current_title, operation.title, current_title
        if operation.type == "move_note":
            return (
                note.metadata.folder_path if note else "",
                operation.folder_path,
                current_title,
            )
        if operation.type in ("add_tag", "remove_tag"):
            return "", f"#{operation.tag}", current_title
        if operation.type == "set_property":
            return "", f"{operation.property_key}: {operation.property_value}", current_title
        if operation.type in ("add_link", "add_relationship"):
            target = operation.target_title or operation.target_note_id or ""
            return "", f"{operation.relationship} → {target}", current_title
        if operation.type in ("delete_note", "archive_note"):
            return current_title, "", current_title
        return "", "", current_title

    @staticmethod
    def _summary(operation: Operation) -> str:
        verb = operation.type.replace("_", " ")
        if operation.title:
            return f"{verb}: {operation.title}"
        if operation.folder_path:
            return f"{verb}: {operation.folder_path}"
        return verb

    # -- apply and undo ------------------------------------------------------

    def apply(
        self,
        review: PlanReview,
        *,
        approved_indexes: list[int],
        allowed_layer_ids: list[str],
    ) -> AppliedPlan:
        """Apply the approved operations in a transaction.

        A snapshot is taken first. If anything fails, the snapshot is restored and
        the workspace is exactly as it was — a half-applied reorganisation is never
        left behind.
        """
        approved = set(approved_indexes)
        operations = [entry for entry in review.entries if entry.index in approved and entry.valid]
        if not operations:
            raise InvalidRequestError("No valid operations were approved.")

        # Re-validate at apply time: the workspace may have changed since review.
        fresh = self.review(review.plan, allowed_layer_ids=allowed_layer_ids)
        fresh_valid = {entry.index for entry in fresh.entries if entry.valid}
        if not approved.issubset(fresh_valid):
            raise ConflictError(
                "The workspace changed since this plan was reviewed. Regenerate it."
            )

        snapshot = self._snapshots.create(f"Before AI: {review.plan.summary}", kind="pre-ai")

        results: list[OperationResult] = []
        try:
            for entry in review.entries:
                if entry.index not in approved or not entry.valid:
                    continue
                operation = review.plan.operations[entry.index]
                result = self._execute(operation, entry.index)
                results.append(result)
                if not result.applied:
                    raise InvalidRequestError(result.error or "An operation failed.")
        except Exception as exc:
            logger.warning("operations.rolled_back", plan_id=review.plan.id)
            self._snapshots.restore(snapshot.id, take_safety_snapshot=False)
            self._snapshots.delete(snapshot.id)
            raise InvalidRequestError(
                "The plan could not be applied and was rolled back. Nothing changed."
            ) from exc

        applied = AppliedPlan(
            plan_id=review.plan.id,
            snapshot_id=snapshot.id,
            applied_at=_now(),
            results=results,
            summary=review.plan.summary,
            provider=review.plan.provider,
            model=review.plan.model,
            prompt=review.plan.prompt,
        )
        self._applied.append(applied)
        logger.info(
            "operations.applied",
            plan_id=review.plan.id,
            operations=applied.applied_count,
            provider=review.plan.provider,
        )
        return applied

    def _execute(self, operation: Operation, index: int) -> OperationResult:
        def ok(note_id: str | None, detail: str) -> OperationResult:
            return OperationResult(
                index=index, type=operation.type, applied=True, note_id=note_id, detail=detail
            )

        try:
            if operation.type == "create_folder":
                folder = self._notes.create_folder(operation.layer_id, "", operation.folder_path)
                return ok(None, f"Created folder {folder.path}")

            if operation.type in ("create_note", "create_task"):
                content = operation.content
                properties: dict[str, object] = (
                    {"type": "task"} if operation.type == "create_task" else {}
                )
                note = self._notes.create_note(
                    layer_id=operation.layer_id,
                    folder_path=operation.folder_path,
                    title=operation.title,
                    content=content,
                    properties=properties,
                )
                return ok(note.metadata.id, f"Created {note.metadata.title}")

            if operation.type == "update_note":
                note = self._notes.update_note(operation.note_id or "", operation.content)
                return ok(note.metadata.id, f"Updated {note.metadata.title}")

            if operation.type == "append_note":
                current = self._notes.get_note(operation.note_id or "")
                note = self._notes.update_note(
                    operation.note_id or "", current.content.rstrip() + "\n\n" + operation.content
                )
                return ok(note.metadata.id, f"Appended to {note.metadata.title}")

            if operation.type == "rename_note":
                note, _rewritten = self._notes.rename_note(operation.note_id or "", operation.title)
                return ok(note.metadata.id, f"Renamed to {note.metadata.title}")

            if operation.type == "move_note":
                note = self._notes.move_note(operation.note_id or "", operation.folder_path)
                return ok(note.metadata.id, f"Moved to {operation.folder_path or 'root'}")

            if operation.type in ("add_tag", "remove_tag"):
                note = self._notes.get_note(operation.note_id or "")
                tags = list(note.metadata.tags)
                if operation.type == "add_tag" and operation.tag not in tags:
                    tags.append(operation.tag)
                elif operation.type == "remove_tag" and operation.tag in tags:
                    tags.remove(operation.tag)
                updated = self._notes.update_properties(
                    operation.note_id or "", {**note.metadata.properties, "tags": tags}
                )
                return ok(
                    updated.metadata.id, f"{operation.type.replace('_', ' ')} #{operation.tag}"
                )

            if operation.type == "set_property":
                note = self._notes.get_note(operation.note_id or "")
                updated = self._notes.update_properties(
                    operation.note_id or "",
                    {**note.metadata.properties, operation.property_key: operation.property_value},
                )
                return ok(updated.metadata.id, f"Set {operation.property_key}")

            if operation.type in ("add_link", "add_relationship"):
                return self._apply_link(operation, index)

            if operation.type == "archive_note":
                note = self._notes.get_note(operation.note_id or "")
                updated = self._notes.update_properties(
                    operation.note_id or "", {**note.metadata.properties, "archived": True}
                )
                return ok(updated.metadata.id, f"Archived {note.metadata.title}")

            if operation.type == "delete_note":
                self._notes.delete_note(operation.note_id or "")
                return ok(operation.note_id, "Moved to trash")

            return OperationResult(
                index=index, type=operation.type, applied=False, error="Unknown operation type."
            )

        except (NotFoundError, ConflictError, InvalidRequestError) as exc:
            return OperationResult(
                index=index, type=operation.type, applied=False, error=exc.message
            )

    def _apply_link(self, operation: Operation, index: int) -> OperationResult:
        """Add a typed link by appending a `relationship:: [[Target]]` line."""
        note = self._notes.get_note(operation.note_id or "")
        target_title = operation.target_title
        if not target_title and operation.target_note_id:
            target = self._notes.get_note(operation.target_note_id)
            target_title = target.metadata.title
        if not target_title:
            return OperationResult(
                index=index, type=operation.type, applied=False, error="No link target."
            )

        relationship = operation.relationship or "references"
        line = f"\n{relationship}:: [[{target_title}]]\n"
        updated = self._notes.update_note(operation.note_id or "", note.content.rstrip() + line)
        return OperationResult(
            index=index,
            type=operation.type,
            applied=True,
            note_id=updated.metadata.id,
            detail=f"{relationship} → {target_title}",
        )

    # -- undo and audit ------------------------------------------------------

    def undo(self, plan_id: str) -> AppliedPlan:
        applied = next(
            (
                item
                for item in reversed(self._applied)
                if item.plan_id == plan_id and not item.undone
            ),
            None,
        )
        if applied is None:
            raise NotFoundError("No applied plan to undo.")

        self._snapshots.restore(applied.snapshot_id, take_safety_snapshot=False)
        applied.undone = True
        logger.info("operations.undone", plan_id=plan_id)
        return applied

    def audit_log(self) -> list[AppliedPlan]:
        """Every AI change, newest first. The reviewable history of what the AI did."""
        return list(reversed(self._applied))


def _preview(text: str) -> str:
    text = text.strip().replace("\n", " ")
    return text[:CONTENT_PREVIEW] + ("…" if len(text) > CONTENT_PREVIEW else "")
