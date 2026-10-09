import asyncio
import base64
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from anonymization_pipeline.pipeline_config import PipelineConfig
from anonymization_pipeline.vlm import (
    VLMClient,
    VLMError,
    VLMModelMissing,
    create_client,
)
from anonymization_pipeline.vlm.responses import (
    entity_schema,
    parse_entities,
    parse_groups,
)
from tests.pipeline import models_file, pipeline_config

SCHEMA = entity_schema(["PERSONA", "CODICE_FISCALE"])

Handler = Callable[[httpx.Request], httpx.Response]


def run(
    cfg: PipelineConfig, handler: Handler, call: Callable[[VLMClient], Awaitable[Any]]
) -> tuple[Any, VLMClient]:
    async def main() -> tuple[Any, VLMClient]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = create_client(cfg, http)
            return await call(client), client

    return asyncio.run(main())


def test_native_ollama_payload(tmp_path: Path) -> None:
    cfg = pipeline_config(
        {"generation": {"options": {"top_k": 1}}},
        models_file=models_file(
            tmp_path,
            {"big:1": 16384, "gemma4:test": 8192},
            {"detection": "gemma4:test"},
        ),
    )
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "message": {"content": '{"entities": []}'},
                "done_reason": "length",
                "prompt_eval_count": 900,
                "eval_count": 10,
            },
        )

    response, client = run(
        cfg, handler, lambda c: c.call("detection", "S", "U", [b"img"], SCHEMA)
    )

    payload = seen[0]
    assert payload["model"] == "gemma4:test"
    assert (
        payload["think"] is False
        and payload["keep_alive"] == "30m"
        and payload["stream"] is False
    )
    assert payload["options"]["num_ctx"] == 8192  # the context of the detection model
    assert payload["options"]["seed"] == 42 and payload["options"]["top_k"] == 1
    assert payload["options"]["num_predict"] == cfg.phases.detection.max_tokens
    assert payload["format"] == SCHEMA
    assert payload["messages"][1]["images"] == [base64.b64encode(b"img").decode()]
    assert response.truncated and response.tokens_in == 900
    assert client.summary()["detection"]["calls"] == 1


def test_ollama_pulls_the_missing_model_of_the_phase_when_allowed(
    tmp_path: Path,
) -> None:
    cfg = pipeline_config(
        {"backend": {"retry_wait_s": 0}},
        vlm_pull_missing=True,
        models_file=models_file(
            tmp_path, {"big:1": 16384, "small:1": 4096}, {"extraction": "small:1"}
        ),
    )
    state = {"pull": 0}
    pulled: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/pull":
            state["pull"] += 1
            pulled.append(json.loads(request.content)["model"])
            return httpx.Response(200, json={"status": "success"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        if state["pull"] == 0:
            return httpx.Response(404, json={"error": "model 'x' not found"})
        return httpx.Response(
            200, json={"message": {"content": "ok"}, "done_reason": "stop"}
        )

    response, _ = run(cfg, handler, lambda c: c.call("extraction", "S", "U"))

    assert response.text == "ok" and pulled == ["small:1"]


def test_ollama_does_not_pull_by_default() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/chat":
            return httpx.Response(404, json={"error": "model not found"})
        return httpx.Response(200, json={"models": []})

    with pytest.raises(VLMModelMissing, match="just models"):
        run(pipeline_config(), handler, lambda c: c.call("extraction", "S", "U"))


def test_retries_on_transient_errors() -> None:
    cfg = pipeline_config({"backend": {"retry_wait_s": 0, "retries": 2}})
    count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        count["n"] += 1
        if count["n"] < 3:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(200, json={"message": {"content": "ok"}})

    response, client = run(cfg, handler, lambda c: c.call("verification", "S", "U"))

    assert response.text == "ok" and client.stats["verification"].errors == 2
    with pytest.raises(VLMError, match="unreachable"):
        run(
            cfg,
            lambda r: httpx.Response(500, json={"error": "x"}),
            lambda c: c.call("verification", "S", "U"),
        )
    with pytest.raises(VLMError, match="HTTP 400"):  # 4xx: no retry
        run(
            cfg,
            lambda r: httpx.Response(400, json={"error": "no images"}),
            lambda c: c.call("verification", "S", "U"),
        )


def test_call_json_retries_then_fails() -> None:
    cfg = pipeline_config({"backend": {"retry_wait_s": 0, "retries": 1}})
    answers = iter(
        ["not json", '{"entities": [{"text": "Mario", "category": "PERSONA"}]}']
    )

    (data, _), _ = run(
        cfg,
        lambda r: httpx.Response(200, json={"message": {"content": next(answers)}}),
        lambda c: c.call_json("detection", "S", "U", None, SCHEMA, parse_entities),
    )

    assert data == [{"text": "Mario", "category": "PERSONA"}]
    with pytest.raises(VLMError, match="invalid model answer"):
        run(
            cfg,
            lambda r: httpx.Response(200, json={"message": {"content": "what"}}),
            lambda c: c.call_json("detection", "S", "U", None, SCHEMA, parse_entities),
        )


def test_truncated_answer_is_not_retried() -> None:
    # temperature 0 and a fixed seed: the same request would be cut at the same point again
    cfg = pipeline_config({"backend": {"retry_wait_s": 0, "retries": 2}})
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "message": {"content": '{"groups": [[1], [1'},
                "done_reason": "length",
            },
        )

    with pytest.raises(VLMError, match="token limit"):
        run(
            cfg,
            handler,
            lambda c: c.call_json(
                "redaction", "S", "U", None, SCHEMA, parse_groups, max_tokens=100
            ),
        )
    assert len(seen) == 1 and seen[0]["options"]["num_predict"] == 100

    # with sampling the next attempt may differ: retried as before
    seen.clear()
    sampled = pipeline_config(
        {
            "backend": {"retry_wait_s": 0, "retries": 2},
            "generation": {"temperature": 0.7},
        }
    )
    with pytest.raises(VLMError, match="invalid model answer"):
        run(
            sampled,
            handler,
            lambda c: c.call_json("redaction", "S", "U", None, SCHEMA, parse_groups),
        )
    assert len(seen) == 3


def test_openai_payload() -> None:
    cfg = pipeline_config(
        {"images": {"format": "jpeg"}},
        vlm_backend="openai",
        vlm_url="http://vllm:8000",
        vlm_api_key="secret",
    )
    seen: list[tuple[str, str | None, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.url.path,
                request.headers.get("authorization"),
                json.loads(request.content),
            )
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "{}"}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 7},
            },
        )

    response, _ = run(
        cfg, handler, lambda c: c.call("verification", "S", "U", [b"img"], SCHEMA)
    )

    path, auth, payload = seen[0]
    assert path == "/v1/chat/completions" and auth == "Bearer secret"
    assert (
        payload["max_tokens"] == cfg.phases.verification.max_tokens
        and payload["seed"] == 42
    )
    assert payload["response_format"]["json_schema"]["schema"] == SCHEMA
    content = payload["messages"][1]["content"]
    assert content[0]["image_url"]["url"].startswith(
        "data:image/jpeg;base64,"
    )  # image before text
    assert content[-1] == {"type": "text", "text": "U"}
    assert response.truncated and response.tokens_out == 7


def test_openai_missing_model() -> None:
    cfg = pipeline_config(vlm_backend="openai", vlm_url="http://vllm:8000/v1")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "other"}]})
        return httpx.Response(404, text="no such model")

    with pytest.raises(VLMModelMissing):
        run(cfg, handler, lambda c: c.call("extraction", "S", "U"))
    with pytest.raises(VLMModelMissing, match="loaded at startup"):
        run(cfg, handler, lambda c: c.pull("gemma4:26b-a4b-it-qat"))


def test_models_and_availability() -> None:
    cfg = pipeline_config()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"models": [{"name": "gemma4"}, {"name": "other:7b"}]}
        )

    (available, ok), client = run(
        cfg, handler, lambda c: asyncio.gather(c.models_available(), c.is_available())
    )

    assert available == {"gemma4:latest", "other:7b"} and ok
    assert client.has_model(available, "gemma4") and client.has_model(
        available, "other:7b"
    )
    assert not client.has_model(available, "gemma4:e4b")
    (ok, _) = run(cfg, lambda r: httpx.Response(500), lambda c: c.is_available())
    assert ok is False


def test_parse_entities_and_groups() -> None:
    assert parse_entities(
        '```json\n{"entities": [{"text": " Mario ", "category": "persona"}]}\n```'
    ) == [{"text": "Mario", "category": "PERSONA"}]
    assert parse_entities('{"entities": []}') == []
    # truncated answer: the complete entries are recovered
    cut = '{"entities": [{"text": "A", "category": "PERSONA"}, {"category": "IBAN", "text": "IT60X"}, {"text": "B'
    assert parse_entities(cut) == [
        {"text": "A", "category": "PERSONA"},
        {"text": "IT60X", "category": "IBAN"},
    ]
    assert parse_entities(
        '{"entities": [{"text": "A", "category": "X"}, {"text": "A", "category": "X"}, {"text": ""}]}'
    ) == [{"text": "A", "category": "X"}]
    assert parse_entities("no json") is None
    assert parse_groups('{"groups": [[1, 2], [3, "x", true]]}') == [[1, 2], [3]]
    assert parse_groups("??") is None
