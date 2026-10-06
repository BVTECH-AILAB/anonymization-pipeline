"""Example service layer for ``Item``."""

from collections.abc import Sequence
from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agenas_service.db.engine import get_session
from agenas_service.db.models import Item
from agenas_service.errors import NotFoundError


class ItemService:
    """CRUD operations on items. One instance per request."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, name: str, description: str) -> Item:
        item = Item(name=name, description=description)
        self._session.add(item)
        await self._session.commit()
        await self._session.refresh(item)
        return item

    async def get(self, item_id: int) -> Item:
        item = await self._session.get(Item, item_id)
        if item is None:
            raise NotFoundError()
        return item

    async def list(self, limit: int) -> Sequence[Item]:
        result = await self._session.scalars(
            select(Item).order_by(Item.id).limit(limit)
        )
        return result.all()


def get_item_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ItemService:
    """FastAPI dependency. Unit tests override it with an in-memory fake."""
    return ItemService(session)


ItemServiceDep = Annotated[ItemService, Depends(get_item_service)]
