from __future__ import annotations

import httpx
import pytest
import respx
from helpers import NOW, comment, serve, serve_null

from hn_tree.client import HNClient, HNError


async def test_get_item_parses_fields(api: respx.MockRouter, client: HNClient) -> None:
    serve(api, comment(2, "alice", "hi", 21, 22, parent=1))
    item = await client.get_item(2)
    assert item is not None
    assert (item.by, item.text, item.kids, item.parent) == ("alice", "hi", (21, 22), 1)


async def test_null_item_is_none(api: respx.MockRouter, client: HNClient) -> None:
    serve_null(api, 6)
    assert await client.get_item(6) is None


async def test_items_are_cached(api: respx.MockRouter, client: HNClient) -> None:
    serve(api, comment(2, "alice", "hi"))
    await client.get_item(2)
    await client.get_item(2)
    assert api.calls.call_count == 1


async def test_retries_server_errors_then_gives_up(api: respx.MockRouter, client: HNClient) -> None:
    route = api.get("item/8.json").respond(503)
    with pytest.raises(HNError, match="giving up after 3 attempts"):
        await client.get_item(8)
    assert route.call_count == 3


async def test_retries_transport_errors(api: respx.MockRouter, client: HNClient) -> None:
    route = api.get("item/2.json").mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200, json=comment(2, "a", "x"))]
    )
    assert await client.get_item(2) is not None
    assert route.call_count == 2


async def test_client_errors_are_not_retried(api: respx.MockRouter, client: HNClient) -> None:
    route = api.get("item/2.json").respond(404)
    with pytest.raises(HNError, match="HTTP 404"):
        await client.get_item(2)
    assert route.call_count == 1


async def test_malformed_item_raises(api: respx.MockRouter, client: HNClient) -> None:
    api.get("item/2.json").respond(json={"by": "no id"})
    with pytest.raises(HNError, match="malformed"):
        await client.get_item(2)


async def test_story_ids(api: respx.MockRouter, client: HNClient) -> None:
    api.get("topstories.json").respond(json=[3, 1, "x", True, 2])
    assert await client.get_story_ids("top") == [3, 1, 2]


async def test_unknown_story_list(client: HNClient) -> None:
    with pytest.raises(ValueError, match="unknown story list"):
        await client.get_story_ids("hot")


async def test_week_asks_search_for_the_last_seven_days(
    api: respx.MockRouter, client: HNClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("hn_tree.client.time.time", lambda: NOW)
    route = api.get("search").respond(
        json={"hits": [{"objectID": "30"}, {"objectID": "10"}, {"objectID": "x"}, {}, "junk"]}
    )
    assert await client.get_story_ids("week") == [30, 10]
    params = route.calls.last.request.url.params
    assert params["tags"] == "story"
    assert params["numericFilters"] == f"created_at_i>{NOW - 7 * 86400}"


async def test_week_rejects_unexpected_search_response(
    api: respx.MockRouter, client: HNClient
) -> None:
    api.get("search").respond(json={"message": "nope"})
    with pytest.raises(HNError, match="expected a list of hits"):
        await client.get_story_ids("week")
