"""Safe request-scoped diagnostic context for selected copilot workflows."""

import logging
import re
from contextvars import ContextVar, Token
from typing import Optional
from uuid import UUID, uuid4

from fastapi import Request


_REQUEST_TRACE_ID: ContextVar[Optional[str]] = ContextVar(
    "request_trace_id", default=None,
)
_SAFE_LABEL = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def safe_diagnostic_label(value) -> Optional[str]:
    return value if isinstance(value, str) and _SAFE_LABEL.fullmatch(value) else None


def request_trace_id(request: Request) -> str:
    requested_id = request.headers.get("X-Request-ID", "")
    try:
        return str(UUID(requested_id))
    except (ValueError, AttributeError):
        return str(uuid4())


def set_request_trace_id(trace_id: str) -> Token:
    return _REQUEST_TRACE_ID.set(trace_id)


def reset_request_trace_id(token: Token) -> None:
    _REQUEST_TRACE_ID.reset(token)


def current_request_trace_id() -> str:
    return _REQUEST_TRACE_ID.get() or "-"


def log_request_diagnostic(
    logger: logging.Logger,
    operation: str,
    trace_id: str,
    stage: str,
    *,
    category: str = "none",
    reason_code=None,
    http_status=None,
    exception_class=None,
    provider_code=None,
    provider_status=None,
) -> None:
    """Emit bounded metadata only; diagnostic failures must not affect requests."""
    try:
        fields = {
            "operation": operation,
            "trace_id": trace_id,
            "stage": stage,
            "category": category,
            "reason_code": reason_code,
        }
        safe_values = {
            key: safe_diagnostic_label(value) or "unknown"
            for key, value in fields.items()
            if value is not None
        }
        logger.info(
            "operation=%s trace_id=%s stage=%s http_status=%s "
            "exception_class=%s provider_code=%s provider_status=%s category=%s reason_code=%s",
            safe_values["operation"],
            safe_values["trace_id"],
            safe_values["stage"],
            http_status if isinstance(http_status, int) else None,
            safe_diagnostic_label(exception_class),
            provider_code if isinstance(provider_code, int) else None,
            safe_diagnostic_label(provider_status),
            safe_values["category"],
            safe_values.get("reason_code"),
        )
    except Exception:
        return
