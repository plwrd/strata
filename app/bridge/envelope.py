"""The bridge wire protocol.

Every call from the frontend crosses this module. It is the single trust boundary
between untrusted web content and the Python host, so it is deliberately narrow
and deliberately boring.

Request::

    {"v": 1, "requestId": "req_ab12...", "payload": { ... }}

Response::

    {"v": 1, "requestId": "req_ab12...", "ok": true,  "data": { ... }}
    {"v": 1, "requestId": "req_ab12...", "ok": false,
     "error": {"code": "...", "message": "...", "retryable": false, "details": {}}}

Rules enforced here, once, for every method:

* the raw string is size-capped before it is parsed (``MAX_PAYLOAD_BYTES``);
* the envelope is validated, then the payload is validated against the method's
  own Pydantic model;
* the response model is validated on the way out;
* unexpected exceptions become a generic ``internal`` error — no stack trace, no
  filesystem path, no message from the original exception ever reaches the
  frontend (it goes to the local log instead).
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domain.errors import (
    ErrorCode,
    InvalidRequestError,
    PayloadTooLargeError,
    StrataError,
)
from app.infrastructure.logging.logger import get_logger

PROTOCOL_VERSION = 1

# 1 MiB. Note bodies larger than this are streamed in chunks by NotesBridge
# rather than sent in a single envelope.
MAX_PAYLOAD_BYTES = 1024 * 1024

logger = get_logger(__name__)

RequestT = TypeVar("RequestT", bound=BaseModel)
ResponseT = TypeVar("ResponseT", bound=BaseModel)


class EmptyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequestEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: int = Field(default=PROTOCOL_VERSION)
    requestId: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class ResponseEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: int = PROTOCOL_VERSION
    requestId: str
    ok: bool
    data: dict[str, Any] | None = None
    error: ErrorBody | None = None


_UNKNOWN_REQUEST_ID = "req_unknown"


def _error_response(request_id: str, error: StrataError) -> str:
    body = ErrorBody(
        code=error.code,
        message=error.message,
        retryable=error.retryable,
        details=error.details,
    )
    return ResponseEnvelope(requestId=request_id, ok=False, error=body).model_dump_json()


def _ok_response(request_id: str, model: BaseModel) -> str:
    envelope = ResponseEnvelope(
        requestId=request_id,
        ok=True,
        data=model.model_dump(mode="json"),
    )
    return envelope.model_dump_json()


def _parse(raw: str) -> RequestEnvelope:
    if len(raw.encode("utf-8", errors="ignore")) > MAX_PAYLOAD_BYTES:
        raise PayloadTooLargeError(
            "Request payload exceeds the maximum size.",
            details={"limitBytes": MAX_PAYLOAD_BYTES},
        )
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise InvalidRequestError("Request is not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise InvalidRequestError("Request must be a JSON object.")
    try:
        envelope = RequestEnvelope.model_validate(parsed)
    except ValidationError as exc:
        raise InvalidRequestError(
            "Request envelope is invalid.",
            details={"issues": _summarise(exc)},
        ) from exc
    if envelope.v != PROTOCOL_VERSION:
        raise InvalidRequestError(
            "Unsupported protocol version.",
            details={"expected": PROTOCOL_VERSION, "received": envelope.v},
        )
    return envelope


def _summarise(exc: ValidationError) -> list[dict[str, str]]:
    """Field-level validation issues, safe to return: names and types only."""
    return [
        {"field": ".".join(str(part) for part in issue["loc"]), "problem": issue["type"]}
        for issue in exc.errors()[:10]
    ]


def bridge_method(
    request_model: type[RequestT],
) -> Callable[[Callable[[Any, RequestT], ResponseT]], Callable[[Any, str], str]]:
    """Wrap a bridge handler so it speaks the envelope protocol.

    The wrapped handler receives a validated request model and returns a Pydantic
    response model. It never sees raw JSON and never builds an error envelope.
    """

    def decorator(handler: Callable[[Any, RequestT], ResponseT]) -> Callable[[Any, str], str]:
        @functools.wraps(handler)
        def wrapper(self: Any, raw: str) -> str:
            request_id = _UNKNOWN_REQUEST_ID
            try:
                envelope = _parse(raw)
                request_id = envelope.requestId
                try:
                    request = request_model.model_validate(envelope.payload)
                except ValidationError as exc:
                    raise InvalidRequestError(
                        "Request payload is invalid.",
                        details={"issues": _summarise(exc)},
                    ) from exc
                response = handler(self, request)
                return _ok_response(request_id, response)
            except StrataError as error:
                logger.warning(
                    "bridge.error",
                    method=handler.__qualname__,
                    code=error.code.value,
                    request_id=request_id,
                )
                return _error_response(request_id, error)
            except Exception:
                # The real exception (which may reference private paths or
                # content) is logged locally and redacted from the response.
                logger.exception("bridge.unhandled", method=handler.__qualname__)
                return _error_response(
                    request_id,
                    StrataError("An internal error occurred. See the local log for details."),
                )

        return wrapper

    return decorator
