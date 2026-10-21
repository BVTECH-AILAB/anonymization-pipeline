"""AILAB NER client against an in-memory transport: no network.

The canned responses reproduce the shape the staging service returns,
including its preprocessing drift ("Ospedale" -> "EPONYM0") and detokenised
words ("01 / 08 / 1985"). All values are synthetic.
"""

import io
import json
from collections.abc import Callable

import httpx
import pytest

from anonmyzation_pipeline.errors import NerUnavailableError
from anonmyzation_pipeline.main import build_pipeline
from anonmyzation_pipeline.services.anonymization.detect.ner import AilabNER
from tests.conftest import FAKE_CF, make_settings

Handler = Callable[[httpx.Request], httpx.Response]


def entity(group: str, word: str, start: int, end: int, score: float = 0.95) -> dict:
    return {
        "entity_group": group,
        "word": word,
        "score": score,
        "start": start,
        "end": end,
    }


def make_ner(
    handler: Handler,
    max_words: int = 200,
    retries: int = 2,
    waits: list[float] | None = None,
) -> tuple[AilabNER, list]:
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = httpx.Client(
        base_url="http://ner.test", transport=httpx.MockTransport(record)
    )
    sleeps = waits if waits is not None else []
    ner = AilabNER(
        client,
        max_words=max_words,
        retries=retries,
        backoff_seconds=1.0,
        sleep=sleeps.append,
    )
    return ner, requests


def batch(per_query: Callable[[str], list[dict]]) -> Handler:
    """``/batch_ner`` answering each query with ``per_query(query)``."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        queries = json.loads(request.content)["queries"]
        return httpx.Response(
            200,
            json={
                "results": [
                    {"obfuscated_text": "[anonimizzato]", "entities": per_query(q)}
                    for q in queries
                ]
            },
        )

    return handler


def found(ner: AilabNER, text: str) -> list[tuple[str, str]]:
    return [(s.label, text[s.start : s.end]) for s in ner.detect(text)]


def test_entities_are_mapped_and_unknown_labels_ignored() -> None:
    text = "Paziente: Giulia Verdi\nVia Roma 5, 00100 Roma\ndiabete di tipo 2"

    def per_query(q: str) -> list[dict]:
        return [
            entity("NOME", "Giulia", 10, 16),
            entity("COGNOME", "Verdi", 17, 22),
            entity("INDIRIZZO", "Via Roma 5, 00100 Roma", 23, 45),
            entity("MALATTIA", "diabete di tipo 2", 46, 63),
        ]

    ner, _ = make_ner(batch(per_query))

    spans = ner.detect(text)

    assert [(s.label, text[s.start : s.end]) for s in spans] == [
        ("PERSON", "Giulia Verdi"),
        ("ADDRESS", "Via Roma 5, 00100 Roma"),
    ]
    assert {s.source for s in spans} == {"ailab-ner"}
    assert spans[0].score == pytest.approx(0.95)


def test_offsets_shifted_by_the_service_preprocessing_are_realigned() -> None:
    # the service rewrites "Ospedale" as "EPONYM0": later offsets move back by one
    text = "presso Ospedale San Camillo il paziente Mario Rossi nato il 01/08/1985"
    mario = text.index("Mario") - 1
    born = text.index("01/08/1985") - 1

    def per_query(q: str) -> list[dict]:
        return [
            entity("LUOGO", "EPONYM0 San Camillo", 7, 26, 0.53),
            entity("NOME", "Mario", mario, mario + 5),
            entity("COGNOME", "Rossi", mario + 6, mario + 11),
            entity("DATA_NASCITA", "01 / 08 / 1985", born, born + 10),
        ]

    ner, _ = make_ner(batch(per_query))

    assert found(ner, text) == [
        ("PERSON", "Mario Rossi"),
        ("DATE_OF_BIRTH", "01/08/1985"),
    ]


def test_a_repeated_name_is_aligned_to_the_nearest_occurrence() -> None:
    text = "Rossi: visita. Il medico Rossi conferma."
    second = text.rindex("Rossi")

    def per_query(q: str) -> list[dict]:
        return [entity("COGNOME", "Rossi", second - 2, second + 3)]

    ner, _ = make_ner(batch(per_query))

    spans = ner.detect(text)

    assert [(s.start, s.end) for s in spans] == [(second, second + 5)]


def test_chunks_go_in_one_batch_and_offsets_survive_chunking() -> None:
    text = "riga uno con cinque parole\nriga due\nPaziente Mario Rossi oggi\n"

    def per_query(q: str) -> list[dict]:
        i = q.find("Mario Rossi")
        return [entity("NOME", "Mario Rossi", i, i + 11)] if i >= 0 else []

    ner, requests = make_ner(batch(per_query), max_words=5)

    assert found(ner, text) == [("PERSON", "Mario Rossi")]
    assert len(requests) == 1
    assert len(json.loads(requests[0].content)["queries"]) >= 2


def test_blank_text_sends_no_request() -> None:
    ner, requests = make_ner(batch(lambda q: []))

    assert ner.detect(" \n\n ") == []
    assert requests == []


@pytest.mark.parametrize(
    "handler",
    [
        lambda r: httpx.Response(503, json={"detail": "down"}),
        lambda r: httpx.Response(200, json={"results": []}),
        lambda r: httpx.Response(200, text="not json"),
    ],
    ids=["http-error", "missing-results", "not-json"],
)
def test_service_errors_fail_closed(handler: Handler) -> None:
    ner, _ = make_ner(handler)

    with pytest.raises(NerUnavailableError):
        ner.detect("Paziente Mario Rossi")


def test_network_errors_fail_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    ner, _ = make_ner(handler)

    with pytest.raises(NerUnavailableError):
        ner.detect("Paziente Mario Rossi")
    with pytest.raises(NerUnavailableError):
        ner.load()
    assert not ner.ready


def test_ready_after_a_healthy_health_check_only() -> None:
    status = {"code": 503}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(status["code"], json={"status": "ok"})

    ner, _ = make_ner(handler)

    assert not ner.ready
    with pytest.raises(NerUnavailableError):
        ner.load()
    assert not ner.ready
    status["code"] = 200
    ner.load()
    assert ner.ready


def test_without_a_url_the_engine_never_becomes_ready() -> None:
    ner = AilabNER(httpx.Client())

    with pytest.raises(NerUnavailableError):
        ner.load()
    assert not ner.ready


def test_only_detection_endpoints_are_called() -> None:
    # /medical_orgs and /medical_labels change the configuration shared with ai-platform
    ner, requests = make_ner(batch(lambda q: [entity("NOME", "Mario", 9, 14)]))

    ner.load()
    ner.detect("Paziente Mario Rossi")

    assert [(r.method, r.url.path) for r in requests] == [
        ("GET", "/health"),
        ("POST", "/batch_ner"),
    ]


def test_ner_never_logs_document_text(log_stream: io.StringIO) -> None:
    text = f"Paziente Mario Rossi CF {FAKE_CF}"

    def per_query(q: str) -> list[dict]:
        # one entity the client cannot place: the warning must not carry its word
        return [
            entity("NOME", "Mario", 9, 14),
            entity("LUOGO", "Maranello", 0, 9),
            entity("CODICE_FISCALE", f"CF {FAKE_CF}", 21, 40),
        ]

    ner, _ = make_ner(batch(per_query))
    ner.detect(text)
    failing, _ = make_ner(lambda r: httpx.Response(500, text=text))
    with pytest.raises(NerUnavailableError):
        failing.detect(text)

    logs = log_stream.getvalue()
    assert "not aligned" in logs
    for value in ("Mario", "Rossi", "Maranello", FAKE_CF):
        assert value not in logs


def test_build_pipeline_points_the_engine_at_the_configured_service() -> None:
    pipeline = build_pipeline(make_settings(ner_url="http://ner.test:8080"))

    assert isinstance(pipeline.ner, AilabNER)
    assert pipeline.ner._client.base_url.host == "ner.test"
    assert not pipeline.ready
    pipeline.ner.close()
    assert build_pipeline(make_settings(ner_enabled=False)).ner is None


def test_an_unreadable_ca_bundle_does_not_crash(log_stream: io.StringIO) -> None:
    pipeline = build_pipeline(
        make_settings(ner_url="https://ner.test", ner_ca_bundle="/missing/ca.pem")
    )

    assert isinstance(pipeline.ner, AilabNER)
    assert "NER CA bundle not loaded" in log_stream.getvalue()


def flaky(failures: list[httpx.Response | Exception]) -> Handler:
    """``/batch_ner`` failing with ``failures`` in order, then answering."""
    ok = batch(lambda q: [entity("NOME", "Mario", 9, 14)])

    def handler(request: httpx.Request) -> httpx.Response:
        if failures:
            failure = failures.pop(0)
            if isinstance(failure, Exception):
                raise failure
            return failure
        return ok(request)

    return handler


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ReadTimeout("timed out"),
        httpx.ConnectError("refused"),
        httpx.Response(502),
        httpx.Response(503),
        httpx.Response(504),
    ],
    ids=["read-timeout", "connect-error", "502", "503", "504"],
)
def test_transient_errors_are_retried_with_growing_waits(
    failure: httpx.Response | Exception,
) -> None:
    waits: list[float] = []
    ner, requests = make_ner(flaky([failure, failure]), waits=waits)

    assert found(ner, "Paziente Mario Rossi") == [("PERSON", "Mario")]
    assert len(requests) == 3
    assert waits == [1.0, 2.0]


def test_after_the_last_retry_the_request_fails_closed() -> None:
    waits: list[float] = []
    timeouts: list[httpx.Response | Exception] = [
        httpx.ReadTimeout("timed out") for _ in range(3)
    ]
    ner, requests = make_ner(flaky(timeouts), retries=2, waits=waits)

    with pytest.raises(NerUnavailableError):
        ner.detect("Paziente Mario Rossi")
    assert len(requests) == 3
    assert waits == [1.0, 2.0]


@pytest.mark.parametrize("status", [400, 422, 500])
def test_errors_that_a_retry_cannot_fix_fail_at_once(status: int) -> None:
    waits: list[float] = []
    ner, requests = make_ner(lambda r: httpx.Response(status), waits=waits)

    with pytest.raises(NerUnavailableError):
        ner.detect("Paziente Mario Rossi")
    assert len(requests) == 1
    assert waits == []


def test_retries_never_log_document_text(log_stream: io.StringIO) -> None:
    text = f"Paziente Mario Rossi CF {FAKE_CF}"
    ner, _ = make_ner(flaky([httpx.Response(503, text=text)]))

    ner.detect(text)

    logs = log_stream.getvalue()
    assert "NER request retried" in logs
    for value in ("Mario", "Rossi", FAKE_CF):
        assert value not in logs


def test_build_pipeline_passes_the_retry_settings() -> None:
    pipeline = build_pipeline(
        make_settings(
            ner_url="http://ner.test", ner_retries=3, ner_retry_backoff_seconds=0.5
        )
    )

    assert isinstance(pipeline.ner, AilabNER)
    assert (pipeline.ner.retries, pipeline.ner.backoff_seconds) == (3, 0.5)


def fixed(*entities: dict) -> Handler:
    """``/batch_ner`` answering every query with the same entities."""
    return batch(lambda q: list(entities))


@pytest.mark.parametrize(
    ("text", "entities", "expected"),
    [
        (
            "dimette in data odierna Sig. Mocenigo, di anni 64",
            [entity("COGNOME", "Mo", 25, 27), entity("NOME", "##cenigo", 27, 33)],
            [("PERSON", "Mocenigo")],
        ),
        (
            "Paziente: BOTTA DANILO · Campione 263615893",
            [entity("COGNOME", "BOTTA DA", 4, 12), entity("NOME", "##NILO", 12, 16)],
            [("PERSON", "BOTTA DANILO")],
        ),
    ],
    ids=["one-word", "two-words"],
)
def test_word_pieces_are_joined_before_alignment(
    text: str, entities: list[dict], expected: list[tuple[str, str]]
) -> None:
    # offsets drift as in the service answers: only the joined word can be found
    ner, _ = make_ner(fixed(*entities))

    assert found(ner, text) == expected


def test_a_word_piece_after_a_gap_is_not_joined() -> None:
    text = "Indirizzo:\nVia Aurelia 112\nComune:\nRecco"
    ner, _ = make_ner(
        fixed(
            entity("INDIRIZZO", "Via Aurelia 112", 11, 26),
            entity("LUOGO", "##cco", 38, 41),
        )
    )

    assert found(ner, text) == [("ADDRESS", "Via Aurelia 112")]


@pytest.mark.parametrize(
    ("text", "entities", "expected"),
    [
        (
            "Dott.ssa Annalisa Grippo\nVisto: il Direttore",
            [
                entity("NOME", "Annalisa", 9, 17),
                entity("COGNOME", "Grippo Visto", 18, 30),
            ],
            [("PERSON", "Annalisa Grippo")],
        ),
        (
            "Indirizzo:\nVia Aurelia 112\nComune:\nRecco",
            [entity("INDIRIZZO", "Via Aurelia 112 Comune", 11, 33)],
            [("ADDRESS", "Via Aurelia 112")],
        ),
        (
            "Paziente:\nCAIRONI DANILO\nNato il:\n17/08/1996",
            [entity("COGNOME", "CAIRONI DANILO Nato", 10, 29)],
            [("PERSON", "CAIRONI DANILO")],
        ),
        (
            "Dott.ssa Ottavia Sbardella\n(centro somministratore)",
            [
                entity("NOME", "Ottavia", 9, 16),
                entity("COGNOME", "Sbardella", 17, 26),
                entity("COGNOME", "(", 27, 28),
            ],
            [("PERSON", "Ottavia Sbardella")],
        ),
    ],
    ids=["label-with-colon", "address-label", "stop-word", "punctuation"],
)
def test_a_span_stops_where_the_next_line_starts_a_field(
    text: str, entities: list[dict], expected: list[tuple[str, str]]
) -> None:
    # the service reads line breaks as spaces and runs on into the next line
    ner, _ = make_ner(fixed(*entities))

    assert found(ner, text) == expected


def test_a_value_wrapped_on_two_lines_stays_whole() -> None:
    text = "Residente in Via Santa\nCaterina, 19\nTel. 0432 123456"
    ner, _ = make_ner(fixed(entity("INDIRIZZO", "Via Santa Caterina, 19", 13, 35)))

    assert found(ner, text) == [("ADDRESS", "Via Santa\nCaterina, 19")]
