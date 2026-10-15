"""Catalog: the categories a request can switch on or off."""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from anonymization_pipeline.pipeline_config import PipelineConfig, registry

router = APIRouter(tags=["catalog"])


class CategoryOut(BaseModel):
    category: str
    description: str
    group: str
    generic: str | None
    default: bool  # on in the service configuration
    protected: bool  # a request cannot switch it off


@router.get("/categories")
async def categories(request: Request) -> list[CategoryOut]:
    """Every category of the registry, in registry order, with its defaults."""
    cfg: PipelineConfig = request.app.state.pipeline_config
    reg = registry()
    protected = set(cfg.protected())
    group_of = {c: g for g, cats in reg.groups.items() for c in cats}
    return [
        CategoryOut(
            category=c,
            description=d,
            group=group_of[c],
            generic=reg.generic.get(c),
            default=cfg.categories[c],
            protected=c in protected,
        )
        for c, d in reg.descriptions.items()
    ]
