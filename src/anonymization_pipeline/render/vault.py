"""Encrypted pseudonym map (AES-256-GCM) and restore.

The key can be:
  - a random 256-bit key in url-safe base64 (``generate_key``), or
  - any passphrase, from which the key is derived with scrypt (random salt in the file).
The header (algorithm, salt, nonce, document fingerprint) is authenticated as AAD.

The file format is the one of the original anonimizzatore-vlm tool (Italian keys inside the
JSON), so maps produced by it can still be restored.
"""

import base64
import binascii
import hashlib
import json
import os
import time
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

VERSION = 1


class VaultError(ValueError):
    """Missing or wrong key, invalid or tampered map, modified text (Italian message for users)."""


def generate_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def sha256(data: bytes | str) -> str:
    return hashlib.sha256(
        data.encode("utf-8") if isinstance(data, str) else data
    ).hexdigest()


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def _scrypt(secret: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(
        secret.encode("utf-8")
    )


def _key(secret: str, salt: bytes) -> tuple[bytes, str]:
    try:
        raw = base64.urlsafe_b64decode(secret.encode() + b"=" * (-len(secret) % 4))
    except (binascii.Error, ValueError):
        raw = b""
    if len(raw) == 32 and len(secret.strip()) in (43, 44):
        return raw, "raw"
    return _scrypt(secret, salt), "scrypt"


def encrypt_map(content: dict[str, Any], secret: str, doc_sha256: str) -> bytes:
    """Encrypt the map of a document.

    Raises:
        VaultError: The key is missing.
    """
    if not secret:
        raise VaultError("Chiave mancante: in modalità pseudonimo serve una chiave")
    salt, nonce = os.urandom(16), os.urandom(12)
    key, kdf = _key(secret, salt)
    header = {
        "v": VERSION,
        "alg": "AES-256-GCM",
        "kdf": kdf,
        "salt": _b64(salt),
        "nonce": _b64(nonce),
        "doc_sha256": doc_sha256,
        "creato": int(time.time()),
    }
    aad = json.dumps(header, sort_keys=True).encode()
    ciphertext = AESGCM(key).encrypt(
        nonce, json.dumps(content, ensure_ascii=False).encode("utf-8"), aad
    )
    return json.dumps({**header, "ct": _b64(ciphertext)}, indent=1).encode()


def decrypt_map(blob: bytes, secret: str) -> dict[str, Any]:
    """Decrypt a map.

    Raises:
        VaultError: Invalid file, wrong key or tampered map.
    """
    try:
        data = json.loads(blob)
        ciphertext = base64.b64decode(data.pop("ct"))
        salt = base64.b64decode(data["salt"])
        nonce = base64.b64decode(data["nonce"])
    except (ValueError, KeyError, TypeError, AttributeError, binascii.Error) as exc:
        raise VaultError("File mappa non valido") from exc
    key = _key(secret, salt)[0] if data.get("kdf") == "raw" else _scrypt(secret, salt)
    aad = json.dumps(data, sort_keys=True).encode()
    try:
        return json.loads(AESGCM(key).decrypt(nonce, ciphertext, aad))
    except (InvalidTag, ValueError) as exc:
        raise VaultError("Chiave errata o mappa manomessa") from exc


def map_content(
    name: str, doc_sha256: str, markdown: str, occurrences: list[dict[str, Any]]
) -> dict[str, Any]:
    """Plain content of the map (it holds the original values: it is only stored encrypted).

    Args:
        name: Original file name.
        doc_sha256: Fingerprint of the original document.
        markdown: Redacted Markdown.
        occurrences: ``{start, end, replacement, original}`` with offsets in ``markdown``.
    """
    return {
        "versione": VERSION,
        "documento": name,
        "sha256": doc_sha256,
        "sha256_anonimizzato": sha256(markdown),
        "occorrenze": [
            {
                "inizio": o["start"],
                "fine": o["end"],
                "sostituto": o["replacement"],
                "originale": o["original"],
            }
            for o in occurrences
        ],
    }


def restore_detail(
    markdown: str, content: dict[str, Any]
) -> tuple[str, list[dict[str, Any]]]:
    """Put the original values back.

    For every occurrence, return its position in the redacted Markdown (``start``, ``end``) and in
    the restored text (``original_start``, ``original_end``). If the Markdown is the produced one
    (same fingerprint) the exact offsets are used, even if a tag was already in the original
    document; if it was modified the replacements are searched in order.

    Raises:
        VaultError: A tag of the map is no longer in the text.
    """
    occurrences = sorted(content.get("occorrenze", []), key=lambda o: o["inizio"])
    exact = sha256(markdown) == content.get("sha256_anonimizzato")
    parts: list[str] = []
    positions: list[dict[str, Any]] = []
    cursor = length = 0
    for o in occurrences:
        if exact:
            start, end = o["inizio"], o["fine"]
        else:
            start = markdown.find(o["sostituto"], cursor)
            if start < 0:
                raise VaultError(
                    "Il testo è stato modificato: un tag della mappa non si trova più"
                )
            end = start + len(o["sostituto"])
        length += start - cursor
        parts += [markdown[cursor:start], o["originale"]]
        positions.append(
            {
                "start": start,
                "end": end,
                "replacement": o["sostituto"],
                "original": o["originale"],
                "original_start": length,
                "original_end": length + len(o["originale"]),
            }
        )
        length += len(o["originale"])
        cursor = end
    parts.append(markdown[cursor:])
    return "".join(parts), positions


def restore(markdown: str, content: dict[str, Any]) -> str:
    """Put the original values back (see ``restore_detail``)."""
    return restore_detail(markdown, content)[0]
