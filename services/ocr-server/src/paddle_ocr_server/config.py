"""OCR server settings: ``OCRSRV_`` environment variables, all with safe defaults."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed server settings."""

    model_config = SettingsConfigDict(env_prefix="OCRSRV_", extra="ignore")

    log_level: str = "INFO"
    # PP-OCRv5 detector: server (default) or mobile; the recogniser is the Latin one
    det_model: str = "PP-OCRv5_server_det"
    # Where RapidOCR keeps its models; empty: its own cache (downloads on first use)
    model_dir: str = ""
    device: Literal["auto", "gpu", "cpu"] = "auto"
    # CPU threads of ONNX Runtime; 0 = one per core
    cpu_threads: int = Field(default=4, ge=0, le=64)
    # On the GPU: memory cap of each model, in MiB (0 = none). The real peak of a
    # 300 dpi page is about 4 GB: memory is released after every run
    gpu_mem_limit_mb: int = Field(default=0, ge=0, le=81920)
    # On the GPU: how cuDNN picks its convolution algorithms
    cudnn_search: Literal["HEURISTIC", "EXHAUSTIVE", "DEFAULT"] = "HEURISTIC"
    # Side strips read again rotated by 90 degrees both ways (vertical text); 0 = off
    margin_pt: float = Field(default=60, ge=0, le=200)
    # Margin words below this confidence are dropped (as in the RapidOCR baseline)
    margin_min_score: float = Field(default=0.5, ge=0, le=1)
    # Rendering resolution assumed when the caller sends no X-Render-DPI header
    default_dpi: float = Field(default=150, ge=50, le=1200)
    max_upload_mb: int = Field(default=40, ge=1, le=200)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings."""
    return Settings()
