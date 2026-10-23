from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import respx
from helpers import BASE_URL, SEARCH_URL

from hn_tree.client import HNClient


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def client() -> AsyncIterator[HNClient]:
    async with HNClient(BASE_URL, search_url=SEARCH_URL, max_attempts=3, backoff_base=0) as hn:
        yield hn


@pytest.fixture
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the CLI's default-configured client retry without sleeping."""
    monkeypatch.setattr("hn_tree.client.random.uniform", lambda a, b: 0.0)
