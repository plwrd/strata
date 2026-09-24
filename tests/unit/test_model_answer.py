"""Reading a model's JSON answer.

Three pipelines depend on this — processing, synthesis, research filing — and
they used to carry a copy each. The cases below are the ones that actually
happen with real models: prose around the object, a brace inside a string, a
truncated answer, and one bad field among good ones.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.services.model_answer import find_json_object, parse_model_json


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = ""
    points: list[str] = Field(default_factory=list)
    score: float = 0.0


def test_the_object_is_found_inside_surrounding_prose() -> None:
    text = 'Sure! Here is the JSON:\n{"summary": "ok"}\nHope that helps.'

    answer, problem = parse_model_json(text, Answer)

    assert answer.summary == "ok"
    assert problem == ""


def test_prose_containing_a_brace_does_not_swallow_the_object() -> None:
    """The bug this scanner replaced: `\\{.*\\}` with DOTALL is greedy, so a
    trailing `}` anywhere in the explanation made a perfectly good answer
    unparseable."""
    text = 'Here: {"summary": "ok"} — note that a set is written {like this}.'

    assert find_json_object(text) == '{"summary": "ok"}'
    answer, problem = parse_model_json(text, Answer)
    assert answer.summary == "ok"
    assert problem == ""


def test_a_brace_inside_a_string_does_not_close_the_object() -> None:
    text = '{"summary": "a } b", "score": 1.0}'

    answer, problem = parse_model_json(text, Answer)

    assert answer.summary == "a } b"
    assert answer.score == 1.0
    assert problem == ""


def test_an_escaped_quote_does_not_end_the_string() -> None:
    text = json.dumps({"summary": 'she said "hi" }'})

    answer, _problem = parse_model_json(text, Answer)

    assert answer.summary == 'she said "hi" }'


def test_nested_objects_are_kept_whole() -> None:
    class Nested(BaseModel):
        model_config = ConfigDict(extra="forbid")

        outer: dict[str, int] = Field(default_factory=dict)

    answer, problem = parse_model_json('{"outer": {"a": 1}} trailing', Nested)

    assert answer.outer == {"a": 1}
    assert problem == ""


@pytest.mark.parametrize(
    "text",
    [
        "no json here at all",
        '{"summary": "truncated',  # the model ran out of tokens
        "",
    ],
)
def test_an_unusable_answer_is_empty_and_says_so(text: str) -> None:
    """Never a guess: a caller that files something anyway is filing its own
    fallback, not the model's noise."""
    answer, problem = parse_model_json(text, Answer)

    assert answer == Answer()
    assert problem


def test_an_object_wrapped_in_an_array_is_still_read() -> None:
    """Models do wrap the answer in a list. The object inside is what was
    asked for, so take it rather than refusing on a technicality."""
    answer, problem = parse_model_json('[{"summary": "list"}]', Answer)

    assert answer.summary == "list"
    assert problem == ""


def test_one_bad_field_does_not_cost_the_good_ones() -> None:
    text = json.dumps({"summary": "kept", "points": "not a list", "score": 0.5})

    answer, problem = parse_model_json(text, Answer)

    assert answer.summary == "kept"
    assert answer.score == 0.5
    assert answer.points == []
    assert "did not fit" in problem


def test_an_answer_with_nothing_salvageable_reports_the_shape() -> None:
    text = json.dumps({"summary": [1, 2], "points": 7})

    answer, problem = parse_model_json(text, Answer)

    assert answer == Answer()
    assert "did not match" in problem


def test_scanning_is_bounded() -> None:
    """A model that rambles for megabytes before the object is not one we can
    use, and the scan must not become the cost."""
    from app.services.model_answer import MAX_SCAN_CHARS

    text = ("x" * (MAX_SCAN_CHARS + 10)) + '{"summary": "too late"}'

    assert find_json_object(text) is None
