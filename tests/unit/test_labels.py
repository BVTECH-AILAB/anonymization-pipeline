from anonymization_pipeline.detectors.labels import LabelsDetector, normalize


def _found(text: str) -> set[tuple[str, str]]:
    return {(p.category, p.value) for p in LabelsDetector().detect(text)}


def test_label_and_value_on_the_line() -> None:
    text = "Paziente: Mario Rossi\nCodice paziente: OSP-2026-048213\nN. nosologico 2026/0048213\nTel. 035 000 0000"

    assert _found(text) >= {
        ("PERSONA", "Mario Rossi"),
        ("ID_PAZIENTE", "OSP-2026-048213"),
        ("NUMERO_CARTELLA", "2026/0048213"),
        ("TELEFONO", "035 000 0000"),
    }


def test_value_on_the_next_line() -> None:
    assert ("COGNOME", "BELLANDI") in _found("COGNOME / SURNAME\nBELLANDI\n")


def test_four_column_markdown_table() -> None:
    text = (
        "| Cognome e nome | **BELLANDI MARCO** | N. nosologico | 2026/0048213 |\n"
        "| :--- | :--- | :--- | :--- |\n"
        "| Medico di base | Dott. Paolo Ghislandi | Medico referente | Dott.ssa Elena Martinelli |\n"
        "| Sesso | M | Provenienza | Pronto Soccorso |"
    )

    assert "Medico di base: Dott. Paolo Ghislandi" in normalize(text)
    assert _found(text) >= {
        ("PERSONA", "BELLANDI MARCO"),
        ("NUMERO_CARTELLA", "2026/0048213"),
        ("MEDICO", "Paolo Ghislandi"),
        ("MEDICO", "Elena Martinelli"),
    }
    assert not any(
        "Pronto Soccorso" in v for _, v in _found(text)
    )  # "Provenienza" is not a label


def test_values_of_the_wrong_shape_are_discarded() -> None:
    text = "Paziente: vigile e collaborante\nRicetta: amoxicillina 1 g\nMedico: 3 volte al giorno"

    assert _found(text) == set()
