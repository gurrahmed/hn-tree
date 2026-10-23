from __future__ import annotations

from typing import Any

import httpx
import respx

BASE_URL = "https://hn.test/v0/"
SEARCH_URL = BASE_URL + "search"
NOW = 1_700_000_000


def serve(router: respx.MockRouter, *items: dict[str, Any]) -> None:
    for item in items:
        router.get(f"item/{item['id']}.json").respond(json=item)


def serve_null(router: respx.MockRouter, item_id: int) -> None:
    router.get(f"item/{item_id}.json").respond(
        content=b"null", headers={"content-type": "application/json"}
    )


def comment(item_id: int, by: str, text: str, *kids: int, **extra: Any) -> dict[str, Any]:
    return {
        "id": item_id,
        "type": "comment",
        "by": by,
        "time": NOW - 600,
        "text": text,
        "kids": list(kids),
        **extra,
    }


STORY = {
    "id": 1,
    "type": "story",
    "by": "op",
    "time": NOW - 3600,
    "title": "Story",
    "url": "https://example.com",
    "score": 10,
    "descendants": 8,
    "kids": [2, 3, 4, 5, 6, 7, 8],
}


def serve_thread(router: respx.MockRouter) -> None:
    """Story 1 with one of each edge case the builder has to handle.

    2  alice "Comment A"      -> 21 "Reply A1", 22 "Reply A2"
    3  deleted, has a reply   -> 31 "Reply B1"
    4  deleted, no replies
    5  dead (flagged)
    6  null from the API
    7  "Comment C", fails once with 500 then succeeds
    8  always fails with 500
    """
    serve(
        router,
        STORY,
        comment(2, "alice", "Comment A", 21, 22),
        comment(21, "bob", "Reply A1"),
        comment(22, "carol", "Reply A2"),
        {"id": 3, "type": "comment", "deleted": True, "time": NOW, "kids": [31]},
        comment(31, "erin", "Reply B1"),
        {"id": 4, "type": "comment", "deleted": True, "time": NOW},
        comment(5, "troll", "flagged text", dead=True),
    )
    serve_null(router, 6)
    router.get("item/7.json").mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(200, json=comment(7, "frank", "Comment C")),
        ]
    )
    router.get("item/8.json").respond(500)
