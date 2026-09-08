"""Reading a model's JSON answer without trusting it.

Every "answer as JSON only" pipeline — processing, synthesis, research filing —
faces the same three problems, and used to solve them three times:

1. **The object is not alone.** Models prepend "Here is the JSON:" and append
   explanations, so the object has to be found rather than parsed from column 0.
2. **The answer may be malformed.** A truncated or hand-mangled object must fail
   to *nothing*, never to a guess.
3. **The answer may be partly right.** One bad field should not discard six good
   ones, so a failed whole-object validation falls back to field-by-field.

Finding the object is done by scanning braces, not by a regex. The obvious
`\\{.*\\}` with DOTALL is greedy: given ``{"a": 1}`` followed by prose that
happens to contain ``}``, it swallows the prose too and the parse fails on an
answer that was perfectly good. The scanner below tracks string literals and
escapes, so a brace inside ``"a } b"`` does not end the object.

Nothing here decides what a bad answer *means* — it reports the problem and lets
the caller choose between keeping less and keeping nothing.
"""

from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

ModelT = TypeVar("ModelT", bound=BaseModel)

# A model that rambles for pages before the object is not one we can use, and
# scanning an unbounded string on every attempt is a cost with no upside.
MAX_SCAN_CHARS = 2_000_000


def find_json_object(text: str) -> str | None:
    """The first balanced ``{...}`` in ``text``, or None.

    String-aware: braces inside string literals do not open or close the object,
    and a backslash escapes the next character. An object nested in an array —
    ``[{...}]``, which models do produce — yields the inner object, which is what
    the caller wanted anyway.
    """
    source = text[:MAX_SCAN_CHARS]
    start = source.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(source)):
            char = source[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return source[start : index + 1]
        # Unbalanced from here (truncated answer); there is no later start that
        # could close either, so stop rather than rescanning the same tail.
        start = -1
    return None


def parse_model_json(text: str, schema: type[ModelT]) -> tuple[ModelT, str]:
    """Validate a model's answer into ``schema``.

    Returns the instance and a plain-language problem, empty when the answer
    validated cleanly. An unusable answer yields an *empty* instance — never a
    partly-invented one — so a caller that files something anyway is filing its
    own fallback rather than the model's noise.
    """
    block = find_json_object(text)
    if block is None:
        return schema(), "The model did not return an answer in the expected format."

    try:
        # A balanced `{...}` either fails to parse or parses to a dict, so there
        # is no "not an object" case to handle here.
        payload: dict[str, Any] = json.loads(block)
    except ValueError:
        return schema(), "The model's answer was not valid JSON."

    try:
        return schema.model_validate(payload), ""
    except ValidationError:
        pass

    # Salvage: one bad field should not cost the good ones.
    salvaged = schema()
    kept = 0
    for field in schema.model_fields:
        if field not in payload:
            continue
        try:
            partial = schema.model_validate({field: payload[field]})
        except ValidationError:
            continue
        setattr(salvaged, field, getattr(partial, field))
        kept += 1
    if kept == 0:
        return salvaged, "The model's answer did not match the expected shape."
    return salvaged, "Parts of the model's answer did not fit the expected shape."
