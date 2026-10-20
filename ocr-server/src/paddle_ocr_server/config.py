"""OCR server settings: ``OCRSRV_`` environment variables, all with safe defaults."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed server settings."""

    model_config = SettingsConfigDict(env_prefix="OCRSRV_", extra="ignore")

    log_level: str = "INFO"
    # rapidocr: PP-OCRv5 on ONNX Runtime, word boxes from the recogniser's character
    # positions (best recall and precision on the gold set); paddle: PaddleOCR 3.x
    engine: Literal["paddle", "rapidocr"] = "rapidocr"
    # PP-OCRv5: server detector (as in the RapidOCR baseline), Latin recogniser
    det_model: str = "PP-OCRv5_server_det"
    rec_model: str = "latin_PP-OCRv5_mobile_rec"
    # One sub-folder per model name; empty: the PaddleX cache (downloads on first use)
    model_dir: str = ""
    device: Literal["auto", "gpu", "cpu"] = "auto"
    # rapidocr: CPU threads of ONNX Runtime; 0 = one per core
    cpu_threads: int = Field(default=4, ge=0, le=64)
    # rapidocr on the GPU: how cuDNN picks its convolution algorithms
    cudnn_search: Literal["HEURISTIC", "EXHAUSTIVE", "DEFAULT"] = "HEURISTIC"
    # Long side of the image the detector sees; recognition crops the full image.
    # paddle: without a cap, 300 dpi pages take about 20 s each on the detector.
    # 0: no cap (the full page).
    det_limit_side_len: int = Field(default=2400, ge=0, le=8000)
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
