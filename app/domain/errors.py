"""Explicit error types for Strata.

Every failure that crosses the frontend bridge is represented by a
:class:`StrataError`. The error *code* is a closed enum: the frontend switches on
it, so adding a code is a protocol change.

Security rule: error payloads that leave the process never contain stack traces,
filesystem paths, or decrypted private content. See ``app/bridge/envelope.py``.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    """Closed set of error codes carried across the bridge."""

    INVALID_REQUEST = "invalid_request"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    NOT_FOUND = "not_found"
    PERMISSION_DENIED = "permission_denied"
    LAYER_LOCKED = "layer_locked"
    CONFLICT = "conflict"
    UNSUPPORTED = "unsupported"
    CANCELLED = "cancelled"
    PROVIDER_ERROR = "provider_error"
    INTERNAL = "internal"


class StrataError(Exception):
    """Base class for all errors that are safe to surface to the frontend.

    ``details`` must only ever contain non-sensitive, structured data (counts,
    identifiers, enum values). Never put paths, titles of private objects, or
    decrypted content in it.
    """

    code: ErrorCode = ErrorCode.INTERNAL
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details or {}
        if retryable is not None:
            self.retryable = retryable


class InvalidRequestError(StrataError):
    code = ErrorCode.INVALID_REQUEST


class PayloadTooLargeError(StrataError):
    code = ErrorCode.PAYLOAD_TOO_LARGE


class NotFoundError(StrataError):
    code = ErrorCode.NOT_FOUND


class PermissionDeniedError(StrataError):
    code = ErrorCode.PERMISSION_DENIED


class LayerLockedError(StrataError):
    """Raised whenever an operation would need a key the app does not hold.

    Deliberately generic: it must not reveal whether the requested object exists
    inside the locked layer.
    """

    code = ErrorCode.LAYER_LOCKED


class ConflictError(StrataError):
    code = ErrorCode.CONFLICT


class UnsupportedError(StrataError):
    code = ErrorCode.UNSUPPORTED


class CancelledError(StrataError):
    code = ErrorCode.CANCELLED


class ProviderError(StrataError):
    code = ErrorCode.PROVIDER_ERROR
    retryable = True


class InternalError(StrataError):
    code = ErrorCode.INTERNAL
