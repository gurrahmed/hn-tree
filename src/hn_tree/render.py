"""Draw a Node tree as box-drawing text, or convert it to plain data for JSON."""

from __future__ import annotations

import textwrap
import time
from dataclasses import dataclass
from typing import Any, Final

from .html_text import html_to_paragraphs, html_to_text
from .models import Item, Node, Status

MIN_TEXT_WIDTH: Final = 20

_BRANCH, _LAST_BRANCH = "├── ", "└── "
_PIPE, _SPACE = "│   ", "    "
_STORY_TYPES: Final = frozenset({"story", "job", "poll"})
_AGE_UNITS: Final = (
    ("year", 365 * 86400),
    ("month", 30 * 86400),
    ("day", 86400),
    ("hour", 3600),
    ("minute", 60),
)


@dataclass(frozen=True, slots=True)
class RenderOptions:
    width: int = 100
    color: bool = False
    show_dead: bool = False  # show the text of flagged comments instead of "[flagged]"
    now: float | None = None  # reference time for "3 hours ago"; defaults to the current time


def render_text(root: Node, options: RenderOptions | None = None) -> str:
    """Render ``root`` and its descendants; the result ends with a newline."""
    opts = options or RenderOptions()
    now = time.time() if opts.now is None else opts.now
    paint = _Painter(opts.color)
    out: list[str] = []

    # Iterative so very deep threads can't hit the recursion limit.
    # Entries are (node, prefix inherited from ancestors, is last sibling, is root).
    stack: list[tuple[Node, str, bool, bool]] = [(root, "", True, True)]
    while stack:
        node, prefix, is_last, is_root = stack.pop()
        if is_root:
            head, child_prefix = "", ""
        else:
            head = prefix + (_LAST_BRANCH if is_last else _BRANCH)
            child_prefix = prefix + (_SPACE if is_last else _PIPE)
        # Lines after the first continue the rail down to this node's children, if any.
        body_prefix = child_prefix + ("│ " if node.children else "  ")
        text_width = max(MIN_TEXT_WIDTH, opts.width - len(body_prefix))

        first, *rest = _label(node, text_width, now, opts.show_dead, paint)
        out.append(paint(head, "rail") + first)
        for line in rest:
            if line:
                out.append(paint(body_prefix, "rail") + line)
            else:
                out.append(paint(body_prefix.rstrip(), "rail"))

        last = len(node.children) - 1
        for i in range(last, -1, -1):
            stack.append((node.children[i], child_prefix, i == last, False))
    return "\n".join(out) + "\n"


def tree_to_dict(root: Node) -> dict[str, Any]:
    """Nested dicts suitable for ``json.dumps``; comment HTML is converted to plain text."""
    root_dict = _node_dict(root)
    stack = [(root, root_dict)]
    while stack:
        node, data = stack.pop()
        for child in node.children:
            child_dict = _node_dict(child)
            data["children"].append(child_dict)
            stack.append((child, child_dict))
    return root_dict


def _label(node: Node, width: int, now: float, show_dead: bool, paint: _Painter) -> list[str]:
    """The lines describing one node, before any tree prefix is added."""
    item = node.item
    if node.status is Status.TRUNCATED:
        n = node.hidden_replies
        return [paint(f"… {n} more {'reply' if n == 1 else 'replies'} not shown", "placeholder")]
    if node.status is Status.ERROR:
        return [paint(f"[failed to load item {node.id}: {node.error}]", "placeholder")]
    if node.status is Status.DELETED:
        return [paint("[deleted]", "placeholder")]
    if item is None:
        return [paint("[missing]", "placeholder")]
    if node.status is Status.DEAD and not show_dead:
        return [paint("[flagged]", "placeholder")]

    is_story = item.type in _STORY_TYPES or item.title is not None
    flagged = " " + paint("[flagged]", "placeholder") if node.status is Status.DEAD else ""
    if is_story:
        lines = [paint(line, "title") for line in _wrap(item.title or "(untitled)", width)]
        if item.url:
            lines.append(paint(item.url, "meta"))
        lines.append(_story_meta(item, now, paint) + flagged)
    else:
        author = paint(item.by or "unknown", "author")
        lines = [f"{author} · {paint(_age(item.time, now), 'meta')}{flagged}"]

    for i, para in enumerate(html_to_paragraphs(item.text)):
        if i or is_story:
            lines.append("")
        lines.extend(para.text.split("\n") if para.preformatted else _wrap(para.text, width))
    return lines


def _story_meta(item: Item, now: float, paint: _Painter) -> str:
    parts = []
    if item.score is not None:
        parts.append(paint(f"▲ {item.score}", "score"))
    if item.by:
        parts.append(paint(item.by, "author"))
    parts.append(paint(_age(item.time, now), "meta"))
    if item.descendants is not None:
        n = item.descendants
        parts.append(paint(f"{n} {'comment' if n == 1 else 'comments'}", "meta"))
    return " · ".join(parts)


def _age(timestamp: int | None, now: float) -> str:
    if timestamp is None:
        return "unknown time"
    seconds = max(0, int(now - timestamp))
    for unit, size in _AGE_UNITS:
        if seconds >= size:
            n = seconds // size
            return f"{n} {unit}{'' if n == 1 else 's'} ago"
    return "just now"


def _wrap(text: str, width: int) -> list[str]:
    # Long words (usually URLs) overflow rather than split, so they stay copyable.
    return textwrap.wrap(text, width, break_long_words=False, break_on_hyphens=False) or [""]


def _node_dict(node: Node) -> dict[str, Any]:
    data: dict[str, Any] = {"id": node.id, "status": node.status.value}
    item = node.item
    if item is not None:
        fields = {
            "type": item.type,
            "by": item.by,
            "time": item.time,
            "title": item.title,
            "url": item.url,
            "score": item.score,
            "descendants": item.descendants,
            "text": html_to_text(item.text) or None,
        }
        data.update({k: v for k, v in fields.items() if v is not None})
    if node.status is Status.TRUNCATED:
        data["hidden_replies"] = node.hidden_replies
    if node.error:
        data["error"] = node.error
    data["children"] = []
    return data


class _Painter:
    _CODES: Final = {
        "title": "1",
        "author": "36",
        "score": "33",
        "meta": "2",
        "rail": "2",
        "placeholder": "2;3",
    }

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, style: str) -> str:
        if not self.enabled or not text:
            return text
        return f"\x1b[{self._CODES[style]}m{text}\x1b[0m"
