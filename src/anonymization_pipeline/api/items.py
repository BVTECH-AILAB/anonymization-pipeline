"""Example router. It shows the patterns, delete it when you add your own."""

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from anonymization_pipeline.logging_setup import log_event
from anonymization_pipeline.privacy import redact_user_text
from anonymization_pipeline.services.items import ItemServiceDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/items", tags=["items"])


class ItemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)


class ItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    created_at: datetime


@router.post("", status_code=201)
async def create_item(
    payload: ItemCreate,
    request: Request,
    service: ItemServiceDep,
) -> ItemRead:
    # User text is logged only through redact_user_text, and only at DEBUG.
    logger.debug(
        "Creating item, description: %s",
        redact_user_text(payload.description, request.app.state.settings),
    )
    item = await service.create(payload.name, payload.description)
    log_event(
        logger,
        "item_created",
        {"item_id": item.id, "description_chars": len(payload.description)},
    )
    return ItemRead.model_validate(item)


@router.get("/{item_id}")
async def get_item(
    item_id: int,
    service: ItemServiceDep,
) -> ItemRead:
    return ItemRead.model_validate(await service.get(item_id))


@router.get("")
async def list_items(
    service: ItemServiceDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[ItemRead]:
    return [ItemRead.model_validate(item) for item in await service.list(limit)]
