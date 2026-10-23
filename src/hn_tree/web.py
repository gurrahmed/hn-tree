"""``hn-tree serve``: a web page listing the newest stories, each opening its comment tree."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Coroutine
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import Any, Final, cast
from urllib.parse import urlsplit

from . import __version__
from .builder import build_tree
from .client import DEFAULT_BASE_URL, DEFAULT_SEARCH_URL, HNClient, HNError, ItemNotFoundError
from .models import Node, Status
from .render import tree_to_dict

log = logging.getLogger(__name__)

_ITEM_PATH: Final = re.compile(r"/api/item/([1-9][0-9]{0,11})")


@dataclass(frozen=True, slots=True)
class Settings:
    base_url: str = DEFAULT_BASE_URL
    search_url: str = DEFAULT_SEARCH_URL
    feed: str = "week"  # one of client.STORY_LISTS
    limit: int = 5
    interval: int = 15  # seconds between the page's feed polls
    max_depth: int | None = None
    max_nodes: int | None = None
    show_dead: bool = False
    concurrency: int = 8
    timeout: float = 10.0


async def load_feed(settings: Settings) -> list[dict[str, Any]]:
    """The first ``settings.limit`` live stories of ``settings.feed``, without their comments."""
    async with _client(settings) as client:
        ids = await client.get_story_ids(settings.feed)
        # The newest stories are sometimes already deleted or killed, so read some spares.
        candidates = ids[: settings.limit * 2]
        results = await asyncio.gather(
            *(client.get_item(item_id) for item_id in candidates), return_exceptions=True
        )

    stories: list[dict[str, Any]] = []
    for item_id, result in zip(candidates, results, strict=True):
        if isinstance(result, HNError):
            log.warning("could not load item %d: %s", item_id, result)
        elif isinstance(result, BaseException):
            raise result
        elif result is not None:
            node = Node.from_item(result)
            if node.status is Status.OK:
                stories.append(tree_to_dict(node))
    return stories[: settings.limit]


async def load_tree(settings: Settings, item_id: int) -> dict[str, Any]:
    """``item_id`` and its replies, as ``hn-tree item --format json`` prints them."""
    async with _client(settings) as client:
        root = await build_tree(
            client,
            item_id,
            max_depth=settings.max_depth,
            max_nodes=settings.max_nodes,
            show_dead=settings.show_dead,
        )
    return tree_to_dict(root)


def make_server(settings: Settings, host: str, port: int) -> WebServer:
    """Bind (port 0 picks a free one) without serving yet; call ``serve_forever()`` next."""
    return WebServer((host, port), settings)


class WebServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], settings: Settings) -> None:
        super().__init__(address, _Handler)
        self.settings = settings
        self.page = _page(settings)


class _Handler(BaseHTTPRequestHandler):
    server_version = f"hn-tree/{__version__}"

    def do_GET(self) -> None:
        server = cast(WebServer, self.server)
        path = urlsplit(self.path).path
        if path == "/":
            self._send(HTTPStatus.OK, server.page, "text/html; charset=utf-8")
        elif path == "/api/feed":
            self._send_json(load_feed(server.settings))
        elif match := _ITEM_PATH.fullmatch(path):
            self._send_json(load_tree(server.settings, int(match[1])))
        else:
            self._send_error(HTTPStatus.NOT_FOUND, "not found")

    def _send_json(self, load: Coroutine[Any, Any, object]) -> None:
        # Each request gets its own event loop and client, so a reload never sees a stale cache.
        try:
            data = asyncio.run(load)
        except ItemNotFoundError as exc:
            self._send_error(HTTPStatus.NOT_FOUND, str(exc))
        except HNError as exc:
            log.warning("%s: %s", self.path, exc)
            self._send_error(HTTPStatus.BAD_GATEWAY, str(exc))
        else:
            body = json.dumps(data, ensure_ascii=False).encode()
            self._send(HTTPStatus.OK, body, "application/json")

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        body = json.dumps({"error": message}).encode()
        self._send(status, body, "application/json")

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        # The page polls constantly, so request lines only show at -vv.
        log.debug("%s %s", self.address_string(), format % args)


def _client(settings: Settings) -> HNClient:
    return HNClient(
        settings.base_url,
        search_url=settings.search_url,
        concurrency=settings.concurrency,
        timeout=settings.timeout,
    )


def _page(settings: Settings) -> bytes:
    config = {
        "feed": settings.feed,
        "limit": settings.limit,
        "interval": settings.interval,
        "showDead": settings.show_dead,
    }
    # Escape "<" so nothing in the data can close the inline <script> it sits in.
    data = json.dumps(config).replace("<", "\\u003c")
    template = files("hn_tree").joinpath("static/index.html").read_text(encoding="utf-8")
    return template.replace("{{config}}", data).encode()
