"""AI generation of operation plans and whole workspaces.

Turns a prompt into a validated :class:`OperationPlan`. This is where the model's
free-text answer meets the schema: the model is asked for JSON, the JSON is parsed
defensively, and anything that does not fit the operation schema is dropped rather
than trusted. A model that returns garbage produces an empty plan, not a crash and
not a rogue edit.

The output is never applied here. It is returned for review — the diff, the
approval, and the transactional apply all happen in :class:`OperationService`.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Literal

from app.domain.errors import ProviderError
from app.domain.ids import new_job_id
from app.domain.operations import Operation, OperationPlan
from app.infrastructure.logging.logger import get_logger
from app.services.ai_service import AIService

logger = get_logger(__name__)

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)

MAX_OPERATIONS = 200

PLAN_INSTRUCTIONS = """You design changes to a personal knowledge workspace as a JSON plan.

Return ONLY a JSON object of this shape, with no prose around it:

{
  "summary": "one short sentence describing the change",
  "operations": [
    {"type": "create_folder", "layer_id": "<id>", "folder_path": "Research", "rationale": "why"},
    {"type": "create_note", "layer_id": "<id>", "folder_path": "Research",
     "title": "A Note", "content": "# A Note\\n\\nBody in Markdown.", "rationale": "why"},
    {"type": "add_tag", "layer_id": "<id>", "note_id": "<existing note id>", "tag": "topic",
     "rationale": "why"},
    {"type": "add_relationship", "layer_id": "<id>", "note_id": "<id>",
     "target_title": "Other Note", "relationship": "supports", "rationale": "why"}
  ]
}

Rules:
- Use ONLY the layer ids and note ids given in the context. Never invent an id.
- Relationship types: references, supports, contradicts, expands, depends_on,
  supersedes, blocks, evidence_for, derived_from, relates_to.
- Prefer additive operations (create, add) over destructive ones (update, delete).
- Every operation needs a short "rationale".
- Keep it focused: a good plan is a dozen operations, not a hundred."""

# Notes mode never needs more files than this; a runaway answer is truncated by
# the review step anyway, but the instructions should not invite one.
MAX_GENERATED_NOTES = 20

GenerationMode = Literal["plan", "notes"]

NOTES_INSTRUCTIONS = """You write new Markdown notes for a personal knowledge workspace \
as a JSON plan.

Return ONLY a JSON object of this shape, with no prose around it:

{
  "summary": "one short sentence describing what was generated",
  "operations": [
    {"type": "create_note", "layer_id": "<id>", "folder_path": "",
     "title": "A Note", "content": "# A Note\\n\\nBody in Markdown.", "rationale": "why"}
  ]
}

Rules:
- Use ONLY the layer ids given in the context. Never invent an id.
- Every operation must be "create_note". Each one becomes a standalone .md file.
- Write complete, self-contained Markdown documents: a single # heading matching the
  title, then well-structured sections with real substance — never a stub, an outline
  of placeholders, or "TODO".
- Titles must be unique, descriptive, and must not repeat an existing note title.
- When source notes are provided, ground the content in them, and end each generated
  note with one final line linking it back to the note it came from, exactly:
  derived_from:: [[<source note title>]]
- Every operation needs a short "rationale"."""


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _try_operation(raw: object) -> Operation | None:
    """Validate one candidate operation; return None if it does not fit the schema."""
    if not isinstance(raw, dict):
        return None
    try:
        return Operation.model_validate(raw)
    except ValueError:
        return None


class AIGenerationService:
    def __init__(self, ai: AIService) -> None:
        self._ai = ai

    async def generate_plan(
        self,
        *,
        provider_id: str,
        model: str,
        prompt: str,
        context: str,
        layer_ids: list[str],
        confirmed_remote: bool = False,
        mode: GenerationMode = "plan",
        note_count: int = 0,
    ) -> OperationPlan:
        """Ask a model for an operation plan and parse it into typed operations.

        Goes through :meth:`AIService.run`, so the policy gate applies: a plan that
        would need to read a locked or local-only layer is refused before the model
        is called, exactly like any other request.

        ``mode="notes"`` swaps the instructions for the note-writing set: the model
        writes whole Markdown documents as ``create_note`` operations instead of
        reorganising. ``note_count`` fixes how many (0 lets the model decide).
        """
        instructions = NOTES_INSTRUCTIONS if mode == "notes" else PLAN_INSTRUCTIONS
        directive = ""
        if mode == "notes":
            count = min(max(note_count, 0), MAX_GENERATED_NOTES)
            directive = (
                f"\n\nCreate exactly {count} note(s): {count} create_note operation(s)."
                if count
                else (
                    "\n\nDecide how many notes the material naturally splits into "
                    f"(between 1 and {MAX_GENERATED_NOTES})."
                )
            )

        request_prompt = (
            f"{prompt.strip()}{directive}\n\n"
            f"Layers you may use: {', '.join(layer_ids)}\n\n"
            "Design the plan now as JSON only."
        )

        full = ""
        async for event in self._ai.run(
            provider_id=provider_id,
            model=model,
            prompt=request_prompt,
            sources=context + "\n\n" + instructions,
            layer_ids=layer_ids,
            confirmed_remote=confirmed_remote,
            max_output_tokens=8000,
            kind="plan-generation",
        ):
            if event.kind == "delta":
                full += event.text
            elif event.kind == "error":
                raise ProviderError(event.error or "The model failed to produce a plan.")

        return self._parse(full, prompt=prompt, provider=provider_id, model=model)

    def _parse(self, text: str, *, prompt: str, provider: str, model: str) -> OperationPlan:
        """Parse the model's answer defensively. Bad operations are dropped, not
        trusted — the review step still validates every survivor against the
        workspace, so this only needs to produce well-formed candidates."""
        match = _JSON_BLOCK.search(text)
        if not match:
            return self._empty(prompt, provider, model, "The model did not return a plan.")

        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return self._empty(prompt, provider, model, "The model's plan was not valid JSON.")

        if not isinstance(payload, dict):
            return self._empty(prompt, provider, model, "The model's plan was not an object.")

        operations: list[Operation] = []
        for raw in payload.get("operations", [])[:MAX_OPERATIONS]:
            # extra="forbid" on Operation means a hallucinated field is a parse
            # error for that one operation — drop it, not the whole plan.
            operation = _try_operation(raw)
            if operation is not None:
                operations.append(operation)

        return OperationPlan(
            id=new_job_id(),
            summary=str(payload.get("summary", ""))[:400],
            operations=operations,
            created_at=_now(),
            provider=provider,
            model=model,
            prompt=prompt,
        )

    @staticmethod
    def _empty(prompt: str, provider: str, model: str, summary: str) -> OperationPlan:
        return OperationPlan(
            id=new_job_id(),
            summary=summary,
            operations=[],
            created_at=_now(),
            provider=provider,
            model=model,
            prompt=prompt,
        )

    def generate_plan_sync(
        self,
        *,
        provider_id: str,
        model: str,
        prompt: str,
        context: str,
        layer_ids: list[str],
        confirmed_remote: bool = False,
        mode: GenerationMode = "plan",
        note_count: int = 0,
    ) -> OperationPlan:
        """Blocking wrapper, for the bridge. Runs the async generation on its own
        loop. Used for plan generation (seconds), never for anything the UI waits on
        interactively."""
        return asyncio.new_event_loop().run_until_complete(
            self.generate_plan(
                provider_id=provider_id,
                model=model,
                prompt=prompt,
                context=context,
                layer_ids=layer_ids,
                confirmed_remote=confirmed_remote,
                mode=mode,
                note_count=note_count,
            )
        )
