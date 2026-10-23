from __future__ import annotations

import httpx
import pytest
import respx
from helpers import comment, serve, serve_null, serve_thread

from hn_tree.builder import build_tree
from hn_tree.client import HNClient, HNError, ItemNotFoundError
from hn_tree.models import Node


def shape(node: Node) -> list[tuple[int | None, str]]:
    return [(child.id, child.status.value) for child in node.children]


async def test_builds_tree_in_kid_order_and_prunes_like_hn(
    api: respx.MockRouter, client: HNClient
) -> None:
    serve_thread(api)
    root = await build_tree(client, 1)

    # 4 (deleted, no replies), 5 (dead) and 6 (null) are hidden; 3 survives for its reply.
    assert shape(root) == [(2, "ok"), (3, "deleted"), (7, "ok"), (8, "error")]
    a, deleted, c, failed = root.children
    assert shape(a) == [(21, "ok"), (22, "ok")]
    assert shape(deleted) == [(31, "ok")]
    assert c.item is not None and c.item.text == "Comment C"  # recovered after one 500
    assert failed.error is not None and "giving up" in failed.error


async def test_show_dead_keeps_hidden_items(api: respx.MockRouter, client: HNClient) -> None:
    serve_thread(api)
    root = await build_tree(client, 1, show_dead=True)
    assert [s for _, s in shape(root)] == [
        "ok",
        "deleted",
        "deleted",
        "dead",
        "missing",
        "ok",
        "error",
    ]


async def test_max_depth_truncates_replies(api: respx.MockRouter, client: HNClient) -> None:
    serve_thread(api)
    root = await build_tree(client, 1, max_depth=1)
    a = root.children[0]
    assert shape(a) == [(None, "truncated")]
    assert a.children[0].hidden_replies == 2
    assert all("item/21" not in str(call.request.url) for call in api.calls)


async def test_max_depth_zero_fetches_only_the_story(
    api: respx.MockRouter, client: HNClient
) -> None:
    serve_thread(api)
    root = await build_tree(client, 1, max_depth=0)
    assert shape(root) == [(None, "truncated")]
    assert root.children[0].hidden_replies == 7
    assert api.calls.call_count == 1


async def test_max_nodes_caps_fetches(api: respx.MockRouter, client: HNClient) -> None:
    serve_thread(api)
    root = await build_tree(client, 1, max_nodes=3)
    # Fetched 2, 3, 4; 4 is pruned; the other four top-level comments are cut.
    assert shape(root) == [(2, "ok"), (3, "deleted"), (None, "truncated")]
    assert root.children[-1].hidden_replies == 4
    assert root.children[0].children[0].hidden_replies == 2  # budget ran out before depth 2
    assert api.calls.call_count == 4


async def test_missing_root_raises(api: respx.MockRouter, client: HNClient) -> None:
    serve_null(api, 99)
    with pytest.raises(ItemNotFoundError):
        await build_tree(client, 99)


async def test_root_fetch_failure_raises(api: respx.MockRouter, client: HNClient) -> None:
    api.get("item/1.json").respond(503)
    with pytest.raises(HNError):
        await build_tree(client, 1)


async def test_cycles_terminate(api: respx.MockRouter, client: HNClient) -> None:
    serve(api, {"id": 1, "type": "story", "title": "S", "kids": [2]}, comment(2, "a", "x", 1))
    root = await build_tree(client, 1)
    assert shape(root) == [(2, "ok")]
    assert root.children[0].children == []


async def test_deep_thread_does_not_recurse(api: respx.MockRouter, client: HNClient) -> None:
    depth = 1500  # well past Python's default recursion limit
    serve(api, {"id": 1, "type": "story", "title": "S", "kids": [2]})

    def chain(request: httpx.Request, item_id: str) -> httpx.Response:
        i = int(item_id)
        return httpx.Response(200, json=comment(i, "a", "x", *([i + 1] if i <= depth else [])))

    api.get(path__regex=r"item/(?P<item_id>\d+)\.json").mock(side_effect=chain)
    root = await build_tree(client, 1)
    assert sum(1 for _ in root.walk()) == depth + 1
