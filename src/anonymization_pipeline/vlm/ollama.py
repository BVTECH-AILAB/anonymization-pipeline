"""Ollama dialect: native ``/api/chat`` API.

The native API is used instead of the OpenAI-compatible one because only here ``num_ctx`` (the
image alone takes hundreds of tokens), ``think`` (a model that reasons by default spends
``num_predict`` on reasoning: the answer comes back empty) and ``keep_alive`` can be set per
request.
The JSON answer is constrained with ``format`` = JSON schema (constrained decoding).

The answer is streamed: when a model does not stop and Ollama aborts it for repetition, the text
received so far is kept as a truncated answer instead of being lost (an OCR model may transcribe
the whole page and then repeat its end).
"""

import base64
import json
import logging
from typing import Any

import httpx

from anonymization_pipeline.pipeline_config.schema import ModelSpec
from anonymization_pipeline.vlm.client import (
    VLMClient,
    VLMError,
    VLMModelMissing,
    VLMResponse,
    VLMTransientError,
)

logger = logging.getLogger(__name__)

# Ollama error that ends a stream when the model keeps repeating the same tokens
_REPEAT_ABORT = "token repeat limit"


def with_tag(name: str) -> str:
    """``/api/tags`` reports ``model:latest`` also for ``model``."""
    return name if ":" in name.rsplit("/", 1)[-1] else f"{name}:latest"


def _model_missing(response: httpx.Response) -> bool:
    if response.status_code != 404:
        return False
    try:
        return "not found" in str(response.json().get("error", "")).lower()
    except ValueError:
        return True


async def _collect(response: httpx.Response) -> VLMResponse:
    """Join the streamed chunks; an abort for repetition keeps the text as a truncated answer.

    Raises:
        VLMTransientError: Another error inside the stream (retried).
    """
    parts: list[str] = []
    last: dict[str, Any] = {}
    async for line in response.aiter_lines():
        if not line.strip():
            continue
        chunk = json.loads(line)
        if "error" in chunk:
            error = str(chunk["error"])
            if _REPEAT_ABORT in error:
                logger.warning(
                    "Model aborted for repetition: answer kept up to the abort (%d characters)",
                    sum(len(p) for p in parts),
                )
                return VLMResponse(text="".join(parts), truncated=True)
            raise VLMTransientError(f"error in the answer stream: {error[:300]}")
        parts.append((chunk.get("message") or {}).get("content") or "")
        last = chunk
    return VLMResponse(
        text="".join(parts),
        truncated=last.get("done_reason") == "length",
        tokens_in=int(last.get("prompt_eval_count") or 0),
        tokens_out=int(last.get("eval_count") or 0),
    )


class OllamaClient(VLMClient):
    kind = "ollama"

    def payload(
        self,
        model: str,
        spec: ModelSpec,
        system: str,
        user: str,
        images: list[bytes],
        schema: dict[str, Any] | None,
        max_tokens: int,
    ) -> dict[str, Any]:
        g = self.generation
        user_message: dict[str, Any] = {"role": "user", "content": user}
        if images:
            user_message["images"] = [base64.b64encode(i).decode() for i in images]
        options: dict[str, Any] = {
            "num_ctx": spec.context,
            "temperature": g.temperature,
            "num_predict": max_tokens,
        }
        if g.seed is not None:
            options["seed"] = g.seed
        payload: dict[str, Any] = {
            "model": model,
            "stream": True,
            "options": {**options, **g.options},
            "messages": [{"role": "system", "content": system}, user_message],
        }
        if g.think is not None:
            payload["think"] = g.think
        if g.keep_alive is not None:
            payload["keep_alive"] = g.keep_alive
        if schema is not None:
            payload["format"] = schema
        return payload

    async def _send(
        self,
        model: str,
        spec: ModelSpec,
        system: str,
        user: str,
        images: list[bytes],
        schema: dict[str, Any] | None,
        max_tokens: int,
        timeout: float,
    ) -> VLMResponse:
        payload = self.payload(model, spec, system, user, images, schema, max_tokens)
        answer = await self._chat(payload, timeout)
        if isinstance(answer, httpx.Response) and _model_missing(answer):
            await self.pull(model)
            answer = await self._chat(payload, timeout)
        if isinstance(answer, httpx.Response):
            self.check(answer)
            raise VLMError(f"unexpected HTTP {answer.status_code} from /api/chat")
        return answer

    async def _chat(
        self, payload: dict[str, Any], timeout: float
    ) -> VLMResponse | httpx.Response:
        """The streamed answer, or the response itself (body read) when the status is an error."""
        async with self._http.stream(
            "POST",
            self.url("/api/chat"),
            json=payload,
            headers=self._headers,
            timeout=timeout,
        ) as response:
            if response.status_code != 200:
                await response.aread()
                return response
            return await _collect(response)

    async def models_available(self) -> set[str]:
        response = await self._get("/api/tags", self.backend.health_timeout_s)
        self.check(response)
        return {with_tag(m["name"]) for m in response.json().get("models", [])}

    def has_model(self, available: set[str], name: str) -> bool:
        return with_tag(name) in available

    async def pull(self, name: str, force: bool = False) -> None:
        """``ollama pull`` of the model ``name``; concurrent calls wait for the first download.

        Args:
            name: Model to download.
            force: Pull even when ``backend.pull_missing`` is off (model download command).

        Raises:
            VLMModelMissing: The model is missing and cannot be downloaded.
        """
        async with self._pull_lock:
            try:
                if self.has_model(await self.models_available(), name):
                    return
            except (httpx.HTTPError, ValueError):
                pass
            if not (force or self.backend.pull_missing):
                raise VLMModelMissing(
                    f"model '{name}' not in Ollama: download it with `just models` "
                    "or set ANONPIP_VLM_PULL_MISSING=true"
                )
            logger.warning(
                "Model '%s' missing in Ollama: downloading (it may take minutes)", name
            )
            response = await self._post(
                "/api/pull",
                {"model": name, "stream": False},
                self.backend.pull_timeout_s,
            )
            if response.status_code != 200:
                try:
                    error = response.json().get("error") or response.text
                except ValueError:
                    error = response.text
                raise VLMModelMissing(f"download of model '{name}' failed: {error}")
            logger.info("Model '%s' downloaded", name)
