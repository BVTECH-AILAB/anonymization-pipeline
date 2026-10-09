"""VLM client (native Ollama or OpenAI-compatible API); the model of each call comes from the
phase (models file)."""

import httpx

from anonymization_pipeline.pipeline_config import PipelineConfig
from anonymization_pipeline.vlm.client import (
    Stats,
    VLMClient,
    VLMError,
    VLMModelMissing,
    VLMResponse,
    gather_or_cancel,
)
from anonymization_pipeline.vlm.images import encode_image
from anonymization_pipeline.vlm.ollama import OllamaClient
from anonymization_pipeline.vlm.openai import OpenAIClient

DIALECTS: dict[str, type[VLMClient]] = {"ollama": OllamaClient, "openai": OpenAIClient}


def create_client(cfg: PipelineConfig, http: httpx.AsyncClient) -> VLMClient:
    """Client of the configured backend for one run, on the shared HTTP connection pool."""
    return DIALECTS[cfg.backend.kind](cfg, http)


__all__ = [
    "Stats",
    "VLMClient",
    "VLMError",
    "VLMModelMissing",
    "VLMResponse",
    "create_client",
    "encode_image",
    "gather_or_cancel",
]
