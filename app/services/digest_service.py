"""Digest a scraped page into a brief, before it is ever saved.

The action behind the browser panel's "Capture as → Brief / Key points". Given
the raw text of a page the user scraped, it asks a model to parse it down to
what is worth keeping and returns a validated :class:`PageDigest`; the caller
renders that to Markdown and stores *only that*, so the workspace fills with
briefs rather than whole web pages.

Same guardrails as every other model call here: the page text goes in through a
neutralised source boundary (it is untrusted input — `render_raw_source`), the
request passes the policy gate in :class:`AIService`, and the answer is
schema-validated with the shared parser — a garbage answer digests to nothing
rather than to invention. No workspace layer content is involved (the source is
a web page the user fetched), so the gate sees no private data leaving.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.domain.digest import DigestMode, PageDigest
from app.domain.errors import ProviderError
from app.domain.ids import new_execution_id
from app.infrastructure.logging.logger import get_logger
from app.services.ai_service import AIService
from app.services.context_export_service import ContextExportService
from app.services.model_answer import parse_model_json

logger = get_logger(__name__)

MAX_INSTRUCTION_CHARS = 500
MAX_KEY_POINTS = 12
MAX_ENTITIES = 12
MAX_DATA_POINTS = 12
MAX_TAGS = 6
MAX_CLAIMS = 8

_BRIEF_INSTRUCTIONS = """You are condensing a web page a researcher scraped, so the page itself can
be thrown away and only your brief kept.

Return ONLY one JSON object of this exact shape, with no prose around it:

{
  "summary": "three or four sentences: what this page is, and what it actually says",
  "key_points": ["the specific things worth remembering, in the page's own terms"],
  "entities": ["people, organisations, products or places the page is about"],
  "data_points": ["concrete figures, dates, prices, versions — the facts, quoted"],
  "tags": ["lowercase-topic-tags"],
  "claims_to_verify": ["assertions that should be checked before being trusted"]
}

Rules:
- Summarise the page's claims AS the page's claims. If it asserts something
  contested or unsupported, say so — do not adopt it as fact.
- Quote figures and names; do not paraphrase a number into vagueness.
- Empty lists are fine. Do not invent detail to fill them."""

_OUTLINE_INSTRUCTIONS = """You are extracting a structured outline from a web page a
researcher scraped, so the page itself can be thrown away and only your outline kept.

Return ONLY one JSON object of this exact shape, with no prose around it:

{
  "summary": "one or two sentences naming what this page is",
  "key_points": ["the substantive points, each a complete standalone statement"],
  "entities": ["people, organisations, products or places named"],
  "data_points": ["concrete figures, dates, prices, versions — quoted exactly"],
  "tags": ["lowercase-topic-tags"],
  "claims_to_verify": ["assertions that should be checked before being trusted"]
}

Rules:
- Prefer completeness of points over prose; the summary stays short.
- Quote figures and names exactly. Attribute claims to the page, never adopt them.
- Empty lists are fine. Do not pad."""


def _now() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


class WebDigestService:
    def __init__(self, ai: AIService, exports: ContextExportService) -> None:
        self._ai = ai
        self._exports = exports

    async def digest(
        self,
        *,
        text: str,
        url: str,
        title: str,
        mode: DigestMode,
        provider_id: str,
        model: str,
        instruction: str = "",
        confirmed_remote: bool = False,
    ) -> tuple[PageDigest, str]:
        """Return the validated digest and the execution id that produced it."""
        if mode == "full":
            raise ProviderError("Full capture needs no digest.")
        if not text.strip():
            raise ProviderError("There is nothing on the page to digest.")

        focus = ""
        cleaned_instruction = instruction.strip()[:MAX_INSTRUCTION_CHARS]
        if cleaned_instruction:
            # The user's focus is an instruction, so it belongs in the
            # instruction channel — never concatenated into the page text.
            focus = f"\n\nFocus especially on: {cleaned_instruction}\n"

        base = _OUTLINE_INSTRUCTIONS if mode == "outline" else _BRIEF_INSTRUCTIONS
        source_block = self._exports.render_raw_source(
            title=title or url, content=text, source_url=url
        )
        context = f"Web page to condense:\n\n{source_block}\n\n{base}{focus}"
        execution_id = new_execution_id()

        full = ""
        async for event in self._ai.run(
            provider_id=provider_id,
            model=model,
            prompt="Condense the page and answer as JSON only.",
            sources=context,
            layer_ids=[],  # a web page, not workspace content — nothing private leaves
            object_count=1,
            private_object_count=0,
            confirmed_remote=confirmed_remote,
            max_output_tokens=4000,
            kind="processing",
            execution_id=execution_id,
        ):
            if event.kind == "delta":
                full += event.text
            elif event.kind == "error":
                raise ProviderError(event.error or "The model failed to digest the page.")

        digest, _problem = parse_model_json(full, PageDigest)
        logger.info(
            "digest.produced",
            mode=mode,
            key_points=len(digest.key_points),
            has_summary=bool(digest.summary.strip()),
        )
        return digest, execution_id

    def digest_sync(self, **kwargs: object) -> tuple[PageDigest, str]:
        """Blocking wrapper for the bridge's worker thread."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.digest(**kwargs))  # type: ignore[arg-type]
        finally:
            loop.close()

    # -- rendering -----------------------------------------------------------

    def render(self, digest: PageDigest, *, mode: DigestMode, title: str, url: str) -> str:
        """The Markdown body that gets saved instead of the whole page."""
        heading = title.strip() or (url or "Web brief")
        label = "Key points" if mode == "outline" else "Brief"
        sections: list[str] = [
            f"# {heading}",
            "",
            f"_{label} of a scraped page — ai-inferred._",
            "",
        ]

        if digest.summary.strip():
            sections += ["## Summary", "", digest.summary.strip(), ""]
        if digest.key_points:
            sections += [
                "## Key points",
                "",
                *[f"- {point}" for point in digest.key_points[:MAX_KEY_POINTS]],
                "",
            ]
        if digest.data_points:
            sections += [
                "## Data points",
                "",
                *[f"- {item}" for item in digest.data_points[:MAX_DATA_POINTS]],
                "",
            ]
        if digest.entities:
            sections += [
                "## Entities",
                "",
                *[f"- {name}" for name in digest.entities[:MAX_ENTITIES]],
                "",
            ]
        if digest.claims_to_verify:
            sections += [
                "## Needs checking",
                "",
                *[f"- {claim}" for claim in digest.claims_to_verify[:MAX_CLAIMS]],
                "",
            ]
        if not (digest.summary.strip() or digest.key_points or digest.data_points):
            sections += ["_The model produced no usable digest of this page._", ""]

        if url:
            sections += ["## Source", "", f"url:: {url}", ""]
        return "\n".join(sections).rstrip() + "\n"

    def clean_tags(self, digest: PageDigest) -> list[str]:
        seen: list[str] = []
        for tag in digest.tags:
            slug = tag.strip().lstrip("#").lower()[:120]
            if slug and slug not in seen:
                seen.append(slug)
        return seen[:MAX_TAGS]
