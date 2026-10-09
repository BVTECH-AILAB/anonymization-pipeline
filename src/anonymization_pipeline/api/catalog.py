"""Options for the requests: models, categories, effective configuration."""

from typing import Any

from fastapi import APIRouter

from anonymization_pipeline.services.anonymization import AnonymizationServiceDep

router = APIRouter(tags=["catalog"])


@router.get("/models", summary="Models in the VLM backend and default model")
async def models(service: AnonymizationServiceDep) -> dict[str, Any]:
    return await service.models()


@router.get(
    "/categories", summary="Category groups and active categories (request options)"
)
async def categories(service: AnonymizationServiceDep) -> dict[str, Any]:
    return service.categories()


@router.get(
    "/configuration", summary="Effective pipeline configuration (without secrets)"
)
async def configuration(service: AnonymizationServiceDep) -> dict[str, Any]:
    return service.configuration()
