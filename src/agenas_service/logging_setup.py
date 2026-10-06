"""Logging setup.

Plain-text lines on stdout. In the cluster Grafana Alloy ships them to Loki,
where KPI-dashboard parses some of them, so the format is a contract:

    2026-10-06 10:00:00,123 INFO agenas_service.api.items [3f2a...]: item_created {"item_id": 1}

Events that a dashboard must read are emitted with ``log_event`` as
``<event_name> {json}``. Renaming an event or one of its keys breaks the
dashboards: cover each event with a test.
"""

import json
import logging
import re
import sys
from contextvars import ContextVar
from typing import Any, TextIO

from agenas_service.config import Settings
from agenas_service.privacy import PiiRedactingFilter

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s [%(request_id)s]: %(message)s"

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_EVENT_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
# Uvicorn installs its own handlers with propagate=False: route them
# through the root handler so they get the same format and redaction.
_FOREIGN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")
_HANDLER_MARK = "_agenas_service_handler"


class RequestIdFilter(logging.Filter):
    """Add the current request id (or ``-``) to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def setup_logging(settings: Settings, stream: TextIO | None = None) -> logging.Handler:
    """Configure the root logger. Safe to call more than once.

    Only the handler installed by a previous call is replaced, so handlers
    added by others (pytest's ``caplog``) are left alone.

    Args:
        settings: Service settings (log level, mask widths).
        stream: Output stream, stdout by default. Tests pass a StringIO.

    Returns:
        The installed handler.
    """
    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, _HANDLER_MARK, False):
            root.removeHandler(existing)

    handler = logging.StreamHandler(stream or sys.stdout)
    setattr(handler, _HANDLER_MARK, True)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(RequestIdFilter())
    handler.addFilter(
        PiiRedactingFilter(settings.log_mask_prefix, settings.log_mask_suffix)
    )
    root.addHandler(handler)
    root.setLevel(settings.log_level)

    for name in _FOREIGN_LOGGERS:
        foreign = logging.getLogger(name)
        foreign.handlers.clear()
        foreign.propagate = True

    return handler


def log_event(logger: logging.Logger, event: str, payload: dict[str, Any]) -> None:
    """Emit a machine-readable event line: ``<event> {json}``.

    The payload must contain only counters, ids, hashes and enums: never
    user text or personal identifiers.

    Args:
        logger: Module logger.
        event: snake_case event name, stable over time.
        payload: JSON-serialisable fields.

    Raises:
        ValueError: If the event name is not snake_case.
    """
    if not _EVENT_NAME.match(event):
        raise ValueError(f"Invalid event name: {event!r}")
    logger.info("%s %s", event, json.dumps(payload, ensure_ascii=False, default=str))
