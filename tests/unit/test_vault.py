import json

import pytest

from anonymization_pipeline.redaction.vault import (
    VaultError,
    decrypt_map,
    encrypt_map,
    generate_key,
    map_content,
    restore,
    restore_detail,
)


@pytest.mark.parametrize("key", [generate_key(), "any passphrase at all"])
def test_round_trip(key: str) -> None:
    blob = encrypt_map({"x": "Mario Rossi"}, key, "abc")

    assert b"Mario" not in blob
    assert decrypt_map(blob, key) == {"x": "Mario Rossi"}


def test_wrong_key_and_tampering() -> None:
    key = generate_key()
    blob = encrypt_map({"x": 1}, key, "abc")

    with pytest.raises(VaultError, match="Chiave errata"):
        decrypt_map(blob, generate_key())
    data = json.loads(blob)
    data["doc_sha256"] = "other"  # authenticated header (AAD)
    with pytest.raises(VaultError, match="manomessa"):
        decrypt_map(json.dumps(data).encode(), key)
    with pytest.raises(VaultError, match="non valido"):
        decrypt_map(b"{}", key)
    with pytest.raises(VaultError, match="mancante"):
        encrypt_map({}, "", "abc")


def _map(redacted: str, occurrences: list[tuple[int, int, str, str]]) -> dict:
    return map_content(
        "doc.pdf",
        "abc",
        redacted,
        [
            {"start": s, "end": e, "replacement": t, "original": o}
            for s, e, t, o in occurrences
        ],
    )


def test_exact_restore_even_with_a_tag_already_in_the_text() -> None:
    # the original document already held the string "[PERSONA_1]"
    original = "Esempio: [PERSONA_1]. Paziente: Mario Rossi"
    redacted = "Esempio: [PERSONA_1]. Paziente: [PERSONA_1]"

    assert (
        restore(redacted, _map(redacted, [(32, 43, "[PERSONA_1]", "Mario Rossi")]))
        == original
    )


def test_restore_on_modified_text() -> None:
    redacted = "A [PERSONA_1] b [CF_1]"
    content = _map(
        redacted, [(2, 13, "[PERSONA_1]", "Mario Rossi"), (16, 22, "[CF_1]", "RSSMRA")]
    )

    assert (
        restore("Titolo aggiunto\n" + redacted, content)
        == "Titolo aggiunto\nA Mario Rossi b RSSMRA"
    )
    with pytest.raises(VaultError, match="modificato"):
        restore("A [PERSONA_1] b", content)


@pytest.mark.parametrize(
    "prefix", ["", "Titolo aggiunto\n"]
)  # Markdown unchanged / modified
def test_restore_with_detail(prefix: str) -> None:
    redacted = "A [PERSONA_1] b [CF_1] c [PERSONA_1]"
    content = _map(
        redacted,
        [
            (2, 13, "[PERSONA_1]", "Mario Rossi"),
            (16, 22, "[CF_1]", "RSSMRA"),
            (25, 36, "[PERSONA_1]", "Rossi"),
        ],
    )
    markdown = prefix + redacted

    text, positions = restore_detail(markdown, content)

    assert text == prefix + "A Mario Rossi b RSSMRA c Rossi"
    for p in positions:
        assert markdown[p["start"] : p["end"]] == p["replacement"]
        assert text[p["original_start"] : p["original_end"]] == p["original"]


def test_map_keeps_the_original_file_format() -> None:
    content = _map("A [X_1]", [(2, 7, "[X_1]", "B")])

    assert set(content) == {
        "versione",
        "documento",
        "sha256",
        "sha256_anonimizzato",
        "occorrenze",
    }
    assert content["occorrenze"] == [
        {"inizio": 2, "fine": 7, "sostituto": "[X_1]", "originale": "B"}
    ]
