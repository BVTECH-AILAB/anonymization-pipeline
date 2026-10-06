"""Unit-test app: real routers and middleware, in-memory item service."""

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from agenas_service.config import Settings
from agenas_service.db.models import Item
from agenas_service.errors import NotFoundError
from agenas_service.main import create_app
from agenas_service.services.items import get_item_service


class FakeItemService:
    """Same interface as ItemService, no database."""

    def __init__(self) -> None:
        self._items: dict[int, Item] = {}

    async def create(self, name: str, description: str) -> Item:
        item = Item(
            id=len(self._items) + 1,
            name=name,
            description=description,
            created_at=datetime.now(UTC),
        )
        self._items[item.id] = item
        return item

    async def get(self, item_id: int) -> Item:
        if item_id not in self._items:
            raise NotFoundError()
        return self._items[item_id]

    async def list(self, limit: int) -> Sequence[Item]:
        return list(self._items.values())[:limit]


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)
    fake = FakeItemService()
    app.dependency_overrides[get_item_service] = lambda: fake
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
