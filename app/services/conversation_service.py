"""Persisted AI conversations — the backend owns the thread.

The frontend only ever sends a conversation id; the turns replayed into a
request come from what Python itself stored. Storage is one JSONL file
(``.strata/ai/conversations.jsonl``), latest full snapshot per id wins, atomic
rewrite on update — conversations are small (turns are capped) so rewriting is
cheaper than being clever.

Redaction: a turn whose request involved a private layer is stored with empty
content and ``redacted: True``. Replaying it contributes nothing to the model
context — the thread keeps its shape, the content stays off disk.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from app.domain.conversations import AIConversation, ConversationTurn
from app.domain.errors import NotFoundError
from app.domain.ids import new_object_id
from app.infrastructure.logging.logger import get_logger
from app.infrastructure.storage.markdown_store import now_iso
from app.infrastructure.storage.paths import replace_atomic
from app.services.workspace_service import WorkspaceService

logger = get_logger(__name__)

CONVERSATIONS_FILE = "conversations.jsonl"
MAX_CONVERSATIONS = 200
MAX_TURNS = 40
_PROMPT_CAP = 32_000
_RESPONSE_CAP = 100_000


class ConversationService:
    def __init__(self, workspace: WorkspaceService) -> None:
        self._workspace = workspace

    def _path(self) -> Path | None:
        if not self._workspace.is_open:
            return None
        return self._workspace.root / ".strata" / "ai" / CONVERSATIONS_FILE

    def _load_all(self) -> dict[str, AIConversation]:
        path = self._path()
        if path is None or not path.is_file():
            return {}
        latest: dict[str, AIConversation] = {}
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return {}
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = AIConversation.model_validate_json(line)
            except ValidationError:
                continue
            latest[record.id] = record
        return latest

    # -- reading -------------------------------------------------------------

    def list_conversations(self) -> list[AIConversation]:
        """Most recently active first."""
        return sorted(
            self._load_all().values(),
            key=lambda conversation: conversation.updated_at,
            reverse=True,
        )

    def get(self, conversation_id: str) -> AIConversation:
        conversation = self._load_all().get(conversation_id)
        if conversation is None:
            raise NotFoundError("That conversation no longer exists.")
        return conversation

    def replayable_turns(self, conversation_id: str) -> list[ConversationTurn]:
        """The turns that may re-enter a model context: non-redacted only."""
        try:
            conversation = self.get(conversation_id)
        except NotFoundError:
            return []
        return [turn for turn in conversation.turns if not turn.redacted]

    # -- writing -------------------------------------------------------------

    def append_turn(
        self,
        conversation_id: str,
        *,
        execution_id: str,
        prompt: str,
        response_text: str,
        provider: str,
        model: str,
        involves_private: bool,
    ) -> AIConversation:
        """Add a turn, creating the conversation if the id is new."""
        path = self._path()
        if path is None:
            # No workspace, nothing durable to append to. The session UI still
            # has its transcript; there is simply no memory to write.
            return AIConversation(id=conversation_id or "conv_unpersisted")

        now = now_iso()
        conversations = self._load_all()
        conversation = conversations.get(conversation_id)
        if conversation is None:
            conversation = AIConversation(
                id=conversation_id or f"conv_{new_object_id()[:16]}",
                created_at=now,
                title=prompt.strip().splitlines()[0][:80] if prompt.strip() else "Conversation",
            )
        turn = (
            ConversationTurn(
                execution_id=execution_id,
                created_at=now,
                prompt="",
                response_text="",
                redacted=True,
            )
            if involves_private
            else ConversationTurn(
                execution_id=execution_id,
                created_at=now,
                prompt=prompt[:_PROMPT_CAP],
                response_text=response_text[:_RESPONSE_CAP],
            )
        )
        conversation.turns = [*conversation.turns, turn][-MAX_TURNS:]
        conversation.updated_at = now
        conversation.provider = provider
        conversation.model = model
        conversations[conversation.id] = conversation

        self._rewrite(conversations)
        return conversation

    def clear(self) -> int:
        """Delete the conversations file. Part of "forget my AI activity"."""
        path = self._path()
        if path is None or not path.is_file():
            return 0
        try:
            path.unlink()
            return 1
        except OSError:
            logger.warning("conversations.clear_failed")
            return 0

    def _rewrite(self, conversations: dict[str, AIConversation]) -> None:
        path = self._path()
        if path is None:
            return
        # Oldest-active first so the newest snapshots win on reload; cap total.
        ordered = sorted(conversations.values(), key=lambda c: c.updated_at)[-MAX_CONVERSATIONS:]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".jsonl.tmp")
            temporary.write_text(
                "\n".join(record.model_dump_json() for record in ordered) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            replace_atomic(temporary, path)
        except OSError:
            logger.warning("conversations.rewrite_failed")
