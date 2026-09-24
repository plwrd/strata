"""Turning a scraped page into something worth keeping.

The point of the digest is *not to keep the page*. A research scrape is mostly
navigation, ads, and boilerplate; saving it whole fills the workspace with noise
you will never read again. So before anything is written, the page can be run
through a model that parses it down to what matters, and only that is stored.

Three modes, from "keep it all" to "keep the least":

* ``full`` — the page's text, verbatim. No model, no digest. The old behaviour,
  still here for when you genuinely want the whole thing.
* ``brief`` — a short prose summary plus the handful of points worth remembering.
* ``outline`` — a structured pull: key points, named entities, concrete data
  points, and the claims that need checking before you trust them.

The model's answer is validated into :class:`PageDigest` and never trusted
further than that — a hallucinated field is dropped, and the untrusted page text
reaches the model only inside a neutralised source boundary, exactly like an
export. What gets saved is a rendered digest carrying its own provenance
(``review_status: ai-inferred``, the execution id, the source URL), so a brief
is never mistaken for something you wrote or verified.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DigestMode = Literal["full", "brief", "outline"]


class PageDigest(BaseModel):
    """A scraped page, parsed down. Every list may be empty; nothing is padded."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(default="", max_length=4000)
    key_points: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    data_points: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    claims_to_verify: list[str] = Field(default_factory=list)
