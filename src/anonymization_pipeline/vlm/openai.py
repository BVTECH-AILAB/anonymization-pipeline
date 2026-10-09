"""OpenAI dialect: ``/v1/chat/completions`` (vLLM, llama.cpp server, LM Studio, ...).

Images as data URIs before the text, answer constrained with
``response_format`` json_schema. ``num_ctx``, ``think`` and ``keep_alive`` do not exist in this
API: the context is set when the server starts (e.g. vLLM ``--max-model-len``). The options in
``generation.options`` are added to the request body as they are (e.g. ``chat_template_kwargs``).
"""

import base64
from typing import Any

from anonymization_pipeline.pipeline_config.schema import ModelSpec
from anonymization_pipeline.vlm.client import VLMClient, VLMModelMissing, VLMResponse


class OpenAIClient(VLMClient):
    kind = "openai"

    def url(self, path: str) -> str:
        base = self.backend.url.rstrip("/")
        return (base if base.endswith("/v1") else base + "/v1") + path

    def payload(
        self,
        model: str,
        system: str,
        user: str,
        images: list[bytes],
        schema: dict[str, Any] | None,
        max_tokens: int,
    ) -> dict[str, Any]:
        g = self.generation
        mime = "image/jpeg" if self.cfg.images.format == "jpeg" else "image/png"
        content: list[dict[str, Any]] = [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime};base64,{base64.b64encode(i).decode()}"
                },
            }
            for i in images
        ]
        content.append({"type": "text", "text": user})
        payload: dict[str, Any] = {
            "model": model,
            "stream": False,
            "temperature": g.temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
        }
        if g.seed is not None:
            payload["seed"] = g.seed
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "answer", "schema": schema, "strict": True},
            }
        return {**payload, **g.options}

    async def _send(
        self,
        model: str,
        spec: ModelSpec,  # noqa: ARG002 - the context is set when the server starts
        system: str,
        user: str,
        images: list[bytes],
        schema: dict[str, Any] | None,
        max_tokens: int,
        timeout: float,
    ) -> VLMResponse:
        response = await self._post(
            "/chat/completions",
            self.payload(model, system, user, images, schema, max_tokens),
            timeout,
        )
        if response.status_code == 404:
            raise VLMModelMissing(f"model '{model}' not served by the backend")
        self.check(response)
        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        usage = data.get("usage") or {}
        return VLMResponse(
            text=(choice.get("message") or {}).get("content") or "",
            truncated=choice.get("finish_reason") == "length",
            tokens_in=int(usage.get("prompt_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or 0),
        )

    async def models_available(self) -> set[str]:
        response = await self._get("/models", self.backend.health_timeout_s)
        self.check(response)
        return {m["id"] for m in response.json().get("data", [])}

    async def pull(self, name: str, force: bool = False) -> None:  # noqa: ARG002 - same signature as Ollama
        """OpenAI-compatible servers load their models at startup: nothing to download.

        Raises:
            VLMModelMissing: The model ``name`` is not served.
        """
        if self.has_model(await self.models_available(), name):
            return
        raise VLMModelMissing(
            f"the openai backend does not download models: '{name}' must be loaded at startup"
        )
