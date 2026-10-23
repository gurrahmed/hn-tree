"""Async client for the official Hacker News API (https://github.com/HackerNews/API)."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from types import TracebackType
from typing import Any, Final

import httpx

from . import __version__
from .models import Item

log = logging.getLogger(__name__)

DEFAULT_BASE_URL: Final = "https://hacker-news.firebaseio.com/v0/"
# hn.algolia.com's search API; the official API has no way to ask for a time range.
DEFAULT_SEARCH_URL: Final = "https://hn.algolia.com/api/v1/search"

# CLI name -> endpoint. Each returns up to 500 ids (200 for ask/show/job).
_API_LISTS: Final = {
    "top": "topstories",
    "new": "newstories",
    "best": "beststories",
    "ask": "askstories",
    "show": "showstories",
    "job": "jobstories",
}
# CLI name -> how far back to look for the most upvoted stories, in seconds.
_POPULAR_LISTS: Final = {"week": 7 * 86400}
_POPULAR_COUNT: Final = 100

STORY_LISTS: Final = (*_API_LISTS, *_POPULAR_LISTS)

_RETRYABLE_STATUS: Final = frozenset({429, 500, 502, 503, 504})
_MAX_RETRY_AFTER: Final = 30.0


class HNError(Exception):
    """A request to the HN API failed and retrying will not help."""


class ItemNotFoundError(HNError):
    def __init__(self, item_id: int) -> None:
        super().__init__(f"item {item_id} does not exist")
        self.item_id = item_id


class HNClient:
    """Shares one connection pool, caps requests in flight, and retries transient failures.

    Use as ``async with HNClient() as client: ...`` so the pool is closed.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        search_url: str = DEFAULT_SEARCH_URL,
        concurrency: int = 8,
        timeout: float = 10.0,
        max_attempts: int = 4,
        backoff_base: float = 0.5,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._semaphore = asyncio.Semaphore(concurrency)
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base
        self._search_url = search_url
        self._cache: dict[int, Item | None] = {}
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            timeout=httpx.Timeout(timeout, connect=min(timeout, 5.0)),
            limits=httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency),
            headers={
                "User-Agent": f"hn-tree/{__version__}",
                "Accept": "application/json",
            },
        )

    async def __aenter__(self) -> HNClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get_item(self, item_id: int) -> Item | None:
        """Fetch one item. ``None`` means the API has no item with that id."""
        if item_id in self._cache:
            return self._cache[item_id]
        data = await self._get_json(f"item/{item_id}.json")
        if data is None:
            item = None
        else:
            try:
                item = Item.from_json(data)
            except ValueError as exc:
                raise HNError(f"item {item_id}: malformed response: {exc}") from exc
        self._cache[item_id] = item
        return item

    async def get_story_ids(self, kind: str) -> list[int]:
        """Ids from one of the story lists in ``STORY_LISTS``, in ranked order."""
        if kind in _POPULAR_LISTS:
            return await self._get_popular_ids(_POPULAR_LISTS[kind])
        try:
            endpoint = _API_LISTS[kind]
        except KeyError:
            raise ValueError(
                f"unknown story list {kind!r}; expected one of {', '.join(STORY_LISTS)}"
            ) from None
        data = await self._get_json(f"{endpoint}.json")
        if not isinstance(data, list):
            raise HNError(f"{endpoint}: expected a list of ids")
        return [i for i in data if isinstance(i, int) and not isinstance(i, bool)]

    async def _get_popular_ids(self, window: int) -> list[int]:
        """Stories posted in the last ``window`` seconds, most points first."""
        # With no query text, Algolia's /search ranks by points, then by comment count.
        params: dict[str, str | int] = {
            "tags": "story",
            "numericFilters": f"created_at_i>{int(time.time()) - window}",
            "hitsPerPage": _POPULAR_COUNT,
        }
        data = await self._get_json(self._search_url, params)
        hits = data.get("hits") if isinstance(data, dict) else None
        if not isinstance(hits, list):
            raise HNError("search: expected a list of hits")
        ids = (hit.get("objectID") if isinstance(hit, dict) else None for hit in hits)
        return [int(i) for i in ids if isinstance(i, str) and i.isascii() and i.isdigit()]

    async def _get_json(self, path: str, params: dict[str, str | int] | None = None) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            retry_after: float | None = None
            try:
                async with self._semaphore:
                    response = await self._http.get(path, params=params)
            except httpx.TransportError as exc:  # connection errors and timeouts
                last_error = exc
            else:
                if response.status_code == httpx.codes.OK:
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise HNError(f"{path}: response was not valid JSON") from exc
                if response.status_code not in _RETRYABLE_STATUS:
                    raise HNError(f"{path}: HTTP {response.status_code}")
                last_error = HNError(f"HTTP {response.status_code}")
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))

            if attempt < self._max_attempts:
                # Sleep outside the semaphore so a backing-off request doesn't hold a slot.
                delay = self._backoff_base * 2 ** (attempt - 1) * random.uniform(0.5, 1.5)
                if retry_after is not None:
                    delay = max(delay, retry_after)
                log.debug(
                    "retrying %s in %.2fs (attempt %d/%d): %s",
                    path,
                    delay,
                    attempt,
                    self._max_attempts,
                    _describe(last_error),
                )
                await asyncio.sleep(delay)

        raise HNError(
            f"{path}: giving up after {self._max_attempts} attempts: {_describe(last_error)}"
        ) from last_error


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None  # HTTP-date form; fall back to our own backoff
    return min(max(seconds, 0.0), _MAX_RETRY_AFTER)


def _describe(exc: Exception | None) -> str:
    if exc is None:
        return "unknown error"
    return str(exc) or type(exc).__name__
