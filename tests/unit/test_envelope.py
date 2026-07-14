"""The bridge protocol itself: the boundary every frontend call crosses."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.bridge.envelope import (
    MAX_PAYLOAD_BYTES,
    PROTOCOL_VERSION,
    bridge_method,
)
from app.domain.errors import LayerLockedError, NotFoundError


class EchoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=100)
    count: int = Field(default=1, ge=1, le=10)


class EchoResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    echoed: str


class Harness:
    """Stands in for a QObject bridge; the decorator does not need Qt."""

    @bridge_method(EchoRequest)
    def echo(self, request: EchoRequest) -> EchoResponse:
        return EchoResponse(echoed=request.text * request.count)

    @bridge_method(EchoRequest)
    def not_found(self, _request: EchoRequest) -> EchoResponse:
        raise NotFoundError("Knowledge object not found.")

    @bridge_method(EchoRequest)
    def locked(self, _request: EchoRequest) -> EchoResponse:
        raise LayerLockedError("This layer is locked.", details={"layerId": "layer_1"})

    @bridge_method(EchoRequest)
    def explodes(self, _request: EchoRequest) -> EchoResponse:
        raise RuntimeError(r"C:\Users\alice\Private\secret plan.md could not be opened")


def envelope(payload: dict[str, object], request_id: str = "req_1") -> str:
    return json.dumps({"v": PROTOCOL_VERSION, "requestId": request_id, "payload": payload})


def test_valid_request_round_trips() -> None:
    response = json.loads(Harness().echo(envelope({"text": "ab", "count": 3})))

    assert response["ok"] is True
    assert response["requestId"] == "req_1"
    assert response["v"] == PROTOCOL_VERSION
    assert response["data"] == {"echoed": "ababab"}


def test_request_id_is_echoed_back() -> None:
    response = json.loads(Harness().echo(envelope({"text": "x"}, request_id="req_zz")))
    assert response["requestId"] == "req_zz"


@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",
        "[]",
        '{"v": 1}',  # no requestId
        '{"v": 99, "requestId": "r", "payload": {}}',  # wrong protocol version
    ],
)
def test_malformed_envelopes_are_rejected(raw: str) -> None:
    response = json.loads(Harness().echo(raw))

    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_request"


def test_unknown_payload_field_is_rejected() -> None:
    # extra="forbid" everywhere: an unexpected field is an error, not something
    # we silently ignore. A silently-ignored field is how a client and a server
    # end up disagreeing about what was requested.
    response = json.loads(Harness().echo(envelope({"text": "x", "injected": True})))

    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_request"


def test_out_of_range_values_are_rejected() -> None:
    response = json.loads(Harness().echo(envelope({"text": "x", "count": 9999})))
    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_request"


@pytest.mark.security()
def test_oversized_payload_is_rejected_before_parsing() -> None:
    huge = json.dumps(
        {"v": 1, "requestId": "req_1", "payload": {"text": "a" * (MAX_PAYLOAD_BYTES + 10)}}
    )

    response = json.loads(Harness().echo(huge))

    assert response["ok"] is False
    assert response["error"]["code"] == "payload_too_large"
    assert response["error"]["details"]["limitBytes"] == MAX_PAYLOAD_BYTES


def test_domain_errors_keep_their_code() -> None:
    response = json.loads(Harness().not_found(envelope({"text": "x"})))
    assert response["error"]["code"] == "not_found"

    response = json.loads(Harness().locked(envelope({"text": "x"})))
    assert response["error"]["code"] == "layer_locked"
    assert response["error"]["details"] == {"layerId": "layer_1"}


@pytest.mark.security()
def test_unexpected_exceptions_are_redacted() -> None:
    """An unhandled error must not leak a path, a filename, or a stack trace."""
    raw = Harness().explodes(envelope({"text": "x"}))
    response = json.loads(raw)

    assert response["ok"] is False
    assert response["error"]["code"] == "internal"
    assert "secret plan" not in raw
    assert "alice" not in raw
    assert "Traceback" not in raw
    assert "RuntimeError" not in raw


def test_error_response_has_no_data_field() -> None:
    response = json.loads(Harness().not_found(envelope({"text": "x"})))
    assert response["data"] is None
    assert response["ok"] is False
