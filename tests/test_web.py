from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest
import respx
from helpers import BASE_URL, SEARCH_URL, STORY, comment, serve, serve_null

from hn_tree.cli import main
from hn_tree.web import Settings, load_feed, load_tree, make_server

SETTINGS = Settings(base_url=BASE_URL, search_url=SEARCH_URL, feed="new", limit=2, interval=30)


def story(item_id: int, title: str, **extra: Any) -> dict[str, Any]:
    return {**STORY, "id": item_id, "title": title, "kids": [], **extra}


async def test_feed_skips_dead_deleted_and_missing_stories(api: respx.MockRouter) -> None:
    api.get("newstories.json").respond(json=[10, 11, 12, 13, 14, 15])
    serve(
        api,
        story(10, "Dead", dead=True),
        {"id": 11, "type": "story", "deleted": True},
        story(13, "First"),
        story(14, "Second"),
        story(15, "Third"),
    )
    serve_null(api, 12)
    stories = await load_feed(Settings(base_url=BASE_URL, feed="new", limit=3))
    assert [s["title"] for s in stories] == ["First", "Second", "Third"]
    assert stories[0]["children"] == []


async def test_feed_reads_only_a_few_spare_ids(api: respx.MockRouter) -> None:
    api.get("newstories.json").respond(json=list(range(10, 30)))
    serve(api, *(story(i, f"Story {i}") for i in range(10, 30)))
    await load_feed(SETTINGS)
    fetched = {str(call.request.url) for call in api.calls}
    assert f"{BASE_URL}item/13.json" in fetched
    assert f"{BASE_URL}item/14.json" not in fetched


async def test_week_feed_keeps_search_order(api: respx.MockRouter) -> None:
    api.get("search").respond(json={"hits": [{"objectID": "20"}, {"objectID": "13"}]})
    serve(api, story(13, "Second", score=900), story(20, "First", score=1000))
    settings = Settings(base_url=BASE_URL, search_url=SEARCH_URL, limit=5)
    assert [s["title"] for s in await load_feed(settings)] == ["First", "Second"]


async def test_tree_matches_cli_json(api: respx.MockRouter) -> None:
    serve(api, {**STORY, "kids": [2]}, comment(2, "alice", "Hello <i>there</i>"))
    tree = await load_tree(SETTINGS, 1)
    assert tree["title"] == "Story"
    assert tree["children"][0]["text"] == "Hello *there*"


@pytest.fixture
def base(api: respx.MockRouter) -> Iterator[str]:
    server = make_server(SETTINGS, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def get(url: str) -> tuple[int, str, bytes]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=5) as response:
            return response.status, response.headers["Content-Type"], response.read()
    except urllib.error.HTTPError as err:
        return err.code, err.headers["Content-Type"], err.read()


def test_page_carries_config(base: str) -> None:
    status, content_type, body = get(base + "/")
    assert status == 200
    assert content_type.startswith("text/html")
    page = body.decode()
    assert "{{config}}" not in page
    assert '"interval": 30' in page


def test_feed_endpoint(api: respx.MockRouter, base: str) -> None:
    api.get("newstories.json").respond(json=[13])
    serve(api, story(13, "First"))
    status, content_type, body = get(base + "/api/feed")
    assert (status, content_type) == (200, "application/json")
    assert [s["id"] for s in json.loads(body)] == [13]


def test_item_endpoint(api: respx.MockRouter, base: str) -> None:
    serve(api, {**STORY, "kids": [2]}, comment(2, "alice", "Hi"))
    status, _, body = get(base + "/api/item/1")
    assert status == 200
    assert json.loads(body)["children"][0]["by"] == "alice"


def test_missing_item_is_404(api: respx.MockRouter, base: str) -> None:
    serve_null(api, 99)
    status, _, body = get(base + "/api/item/99")
    assert status == 404
    assert json.loads(body) == {"error": "item 99 does not exist"}


def test_api_failure_is_502(api: respx.MockRouter, base: str, no_backoff: None) -> None:
    api.get("newstories.json").respond(500)
    status, _, body = get(base + "/api/feed")
    assert status == 502
    assert "HTTP 500" in json.loads(body)["error"]


@pytest.mark.parametrize("path", ["/nope", "/api/item/abc", "/api/item/0", "/api/item/"])
def test_unknown_paths_are_404(base: str, path: str) -> None:
    assert get(base + path)[0] == 404


def test_serve_rejects_bad_port() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["serve", "--port", "70000"])
    assert exc.value.code == 2
