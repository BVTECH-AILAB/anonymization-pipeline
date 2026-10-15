"""Pipeline configuration: schema, layered loading, category registry."""

from anonymization_pipeline.pipeline_config.categories import Registry, registry
from anonymization_pipeline.pipeline_config.loader import (
    ProtectedCategoryError,
    load_pipeline_config,
    request_config,
)
from anonymization_pipeline.pipeline_config.schema import PipelineConfig

__all__ = [
    "PipelineConfig",
    "ProtectedCategoryError",
    "Registry",
    "load_pipeline_config",
    "registry",
    "request_config",
]
