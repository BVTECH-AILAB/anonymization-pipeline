"""Privacy helpers: pseudonymisation, masking and log redaction.

Rules (see docs/LOGGING_AND_PRIVACY.md):

* identifiers that can be linked to a person (codice fiscale, user id from
  the JWT) are logged only through ``hash_identifier`` or
  ``mask_identifier``;
* user-entered text (chat messages, notes, free-text fields) is logged only
  through ``redact_user_text``;
* ``PiiRedactingFilter`` is a safety net for the text we do not control
  (exception messages, third-party logs, tracebacks). It does not replace
  the two rules above.

Ported from authz (``security.py`` and ``log_redaction.py``).
"""

import hashlib
import logging
import re
from typing import Any

from agenas_service.config import Settings

REDACTED = "<redacted>"

# Digits in a codice fiscale may be replaced by these letters (omocodia).
# None of them is a hex digit, so SHA256 digests never match.
_DIGIT = "[0-9LMNPQRSTUV]"

# Alphanumeric lookarounds instead of \b: \b treats "_" as a word character
# and would miss "RSSMRA85M01H501U_lista.csv".
CF_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])"
    rf"[A-Z]{{6}}{_DIGIT}{{2}}[A-Z]{_DIGIT}{{2}}[A-Z]{_DIGIT}{{3}}[A-Z]"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def hash_identifier(value: str) -> str:
    """Pseudonymise an identifier with SHA256.

    The value is stripped and uppercased first, so the same codice fiscale
    typed in a different case hashes to the same digest. This is the same
    normalisation authz uses, so digests can be joined across services.

    Args:
        value: Plaintext identifier, typically a codice fiscale.

    Returns:
        64-character lowercase hex digest.
    """
    return hashlib.sha256(value.strip().upper().encode("utf-8")).hexdigest()


def mask_identifier(identifier: str, prefix: int = 1, suffix: int = 1) -> str:
    """Mask an identifier for human-readable logs.

    Args:
        identifier: Sensitive identifier.
        prefix: Characters kept at the start.
        suffix: Characters kept at the end.

    Returns:
        ``"R***U"`` for ``"RSSMRA85M01H501U"`` with the default widths.
    """
    if not identifier:
        return ""
    head = identifier[:prefix]
    tail = identifier[-suffix:] if suffix else ""
    return f"{head}***{tail}"


def redact_cf(text: str, prefix: int = 1, suffix: int = 1) -> str:
    """Mask every codice fiscale embedded in ``text``."""
    return CF_PATTERN.sub(lambda m: mask_identifier(m.group(0), prefix, suffix), text)


def redact_user_text(text: str, settings: Settings) -> str:
    """Return user-entered text in a form that is safe to log.

    Returns ``<redacted>`` unless ``extended_log_context`` is on in a
    non-prod environment. Even then, codici fiscali are masked.
    """
    if not settings.log_user_text:
        return REDACTED
    return redact_cf(text, settings.log_mask_prefix, settings.log_mask_suffix)


class PiiRedactingFilter(logging.Filter):
    """Mask codici fiscali in a log record, in place.

    Installed on the root handler by ``setup_logging``. A filter on a
    *handler* sees every record that reaches it, including the ones
    propagated from child loggers (a filter on a *logger* would not).
    It never drops a record.
    """

    def __init__(self, prefix: int = 1, suffix: int = 1) -> None:
        super().__init__()
        self._prefix = prefix
        self._suffix = suffix
        self._exc_formatter = logging.Formatter()

    def _redact(self, value: Any) -> Any:
        if isinstance(value, str):
            return redact_cf(value, self._prefix, self._suffix)
        if isinstance(value, BaseException):
            return redact_cf(str(value), self._prefix, self._suffix)
        if isinstance(value, tuple):
            return tuple(self._redact(item) for item in value)
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, dict):
            return {key: self._redact(item) for key, item in value.items()}
        return value

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redact(record.msg)
        if record.args:
            record.args = self._redact(record.args)
        if record.exc_info and not record.exc_text:
            record.exc_text = self._exc_formatter.formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self._redact(record.exc_text)
        if record.stack_info:
            record.stack_info = self._redact(record.stack_info)
        return True
