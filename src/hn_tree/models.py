"""Typed views of Hacker News API items and the comment tree built from them."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeGuard


class Status(StrEnum):
    """What a node in the tree represents."""

    OK = "ok"
    DELETED = "deleted"  # removed by its author or a moderator
    DEAD = "dead"  # flagged or killed; HN hides these by default
    MISSING = "missing"  # the API returned null for this id
    ERROR = "error"  # the fetch failed even after retries
    TRUNCATED = "truncated"  # stands in for replies cut off by --max-depth / --max-nodes


# Statuses HN hides unless something visible hangs beneath them.
HIDDEN_STATUSES = frozenset({Status.DELETED, Status.DEAD, Status.MISSING})


@dataclass(frozen=True, slots=True)
class Item:
    """One record from ``/v0/item/<id>.json``. Every field except ``id`` is optional."""

    id: int
    type: str | None = None
    by: str | None = None
    time: int | None = None
    text: str | None = None  # HTML
    title: str | None = None
    url: str | None = None
    score: int | None = None
    descendants: int | None = None
    kids: tuple[int, ...] = ()  # in HN's ranked display order
    deleted: bool = False
    dead: bool = False
    parent: int | None = None

    @classmethod
    def from_json(cls, data: Any) -> Item:
        if not isinstance(data, dict):
            raise ValueError(f"expected a JSON object, got {type(data).__name__}")
        item_id = data.get("id")
        if not _is_int(item_id):
            raise ValueError("item has no integer 'id'")
        kids = data.get("kids")
        return cls(
            id=item_id,
            type=_opt_str(data, "type"),
            by=_opt_str(data, "by"),
            time=_opt_int(data, "time"),
            text=_opt_str(data, "text"),
            title=_opt_str(data, "title"),
            url=_opt_str(data, "url"),
            score=_opt_int(data, "score"),
            descendants=_opt_int(data, "descendants"),
            kids=tuple(k for k in kids if _is_int(k)) if isinstance(kids, list) else (),
            deleted=data.get("deleted") is True,
            dead=data.get("dead") is True,
            parent=_opt_int(data, "parent"),
        )


@dataclass(slots=True)
class Node:
    """A position in the rendered tree: an item, a placeholder for one, or a truncation marker."""

    id: int | None
    status: Status
    item: Item | None = None
    children: list[Node] = field(default_factory=list)
    hidden_replies: int = 0  # for TRUNCATED nodes: how many replies were not fetched
    error: str | None = None  # for ERROR nodes

    @classmethod
    def from_item(cls, item: Item) -> Node:
        if item.deleted:
            status = Status.DELETED
        elif item.dead:
            status = Status.DEAD
        else:
            status = Status.OK
        return cls(id=item.id, status=status, item=item)

    @classmethod
    def truncated(cls, hidden_replies: int) -> Node:
        return cls(id=None, status=Status.TRUNCATED, hidden_replies=hidden_replies)

    def walk(self) -> Iterator[Node]:
        """Yield this node and its descendants in display order, without recursion."""
        stack = [self]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.children))


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _opt_int(data: dict[str, Any], key: str) -> int | None:
    value = data.get(key)
    return value if _is_int(value) else None


def _opt_str(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) else None
