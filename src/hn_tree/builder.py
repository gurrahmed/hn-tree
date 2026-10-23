"""Turn an item id into a tree of Nodes, fetching one level of the thread at a time."""

from __future__ import annotations

import asyncio
import logging

from .client import HNClient, HNError, ItemNotFoundError
from .models import HIDDEN_STATUSES, Node, Status

log = logging.getLogger(__name__)


async def build_tree(
    client: HNClient,
    item_id: int,
    *,
    max_depth: int | None = None,
    max_nodes: int | None = None,
    show_dead: bool = False,
) -> Node:
    """Fetch ``item_id`` and every reply beneath it.

    All items on one level of the thread are fetched concurrently, so the number of
    round trips follows the depth of the discussion rather than its size. Children keep
    the order of their parent's ``kids`` list, which is HN's ranked display order.

    ``max_depth`` counts reply levels below the root (0 fetches only the root);
    ``max_nodes`` caps how many items are fetched below the root. Replies cut off by
    either limit are represented by a single TRUNCATED node under their parent.

    Raises ``HNError`` if the root itself can't be loaded. Failures below the root
    become ERROR nodes so the rest of the thread still renders.
    """
    item = await client.get_item(item_id)
    if item is None:
        raise ItemNotFoundError(item_id)
    root = Node.from_item(item)

    seen = {item_id}  # the API should never contain cycles, but don't loop forever if it does
    budget = max_nodes
    frontier = [root]
    depth = 0
    while frontier:
        depth += 1
        # (parent, ids to fetch for it, how many of its kids were cut by the node budget)
        batches: list[tuple[Node, list[int], int]] = []
        for parent in frontier:
            own_kids = parent.item.kids if parent.item else ()
            kids = [k for k in dict.fromkeys(own_kids) if k not in seen]
            if not kids:
                continue
            if max_depth is not None and depth > max_depth:
                parent.children.append(Node.truncated(len(kids)))
                continue
            take = kids if budget is None else kids[:budget]
            if budget is not None:
                budget -= len(take)
            seen.update(take)
            batches.append((parent, take, len(kids) - len(take)))

        ids = [kid for _, take, _ in batches for kid in take]
        if ids:
            log.debug("item %d: fetching %d items at depth %d", item_id, len(ids), depth)
        nodes = await asyncio.gather(*(_fetch_node(client, kid) for kid in ids))
        fetched = dict(zip(ids, nodes, strict=True))

        frontier = []
        for parent, take, cut in batches:
            for kid in take:
                node = fetched[kid]
                parent.children.append(node)
                if node.item is not None and node.item.kids:
                    frontier.append(node)
            if cut:
                parent.children.append(Node.truncated(cut))

    if not show_dead:
        prune_hidden(root)
    return root


def prune_hidden(root: Node) -> None:
    """Drop deleted, dead and missing nodes with nothing visible beneath them, as HN does."""
    # Reversed pre-order visits every child before its parent, so emptiness propagates upward.
    for node in reversed(list(root.walk())):
        node.children = [
            child
            for child in node.children
            if child.status not in HIDDEN_STATUSES or child.children
        ]


async def _fetch_node(client: HNClient, item_id: int) -> Node:
    try:
        item = await client.get_item(item_id)
    except HNError as exc:
        log.debug("could not load item %d: %s", item_id, exc)
        return Node(id=item_id, status=Status.ERROR, error=str(exc))
    if item is None:
        return Node(id=item_id, status=Status.MISSING)
    return Node.from_item(item)
