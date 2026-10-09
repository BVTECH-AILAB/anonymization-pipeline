"""Encoding of the images sent to the model (resized to ``max_side_px``)."""

import io

from PIL import Image

from anonymization_pipeline.pipeline_config import PipelineConfig


def encode_image(image: Image.Image, cfg: PipelineConfig) -> bytes:
    c = cfg.images
    side = max(image.size)
    if side > c.max_side_px:
        scale = c.max_side_px / side
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.Resampling.LANCZOS,
        )
    buffer = io.BytesIO()
    if c.format == "jpeg":
        image.convert("RGB").save(buffer, "JPEG", quality=c.jpeg_quality)
    else:
        image.save(buffer, "PNG")
    return buffer.getvalue()
