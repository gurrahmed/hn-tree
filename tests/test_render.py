from __future__ import annotations

from helpers import NOW

from hn_tree.models import Item, Node, Status
from hn_tree.render import RenderOptions, render_text, tree_to_dict

OPTS = RenderOptions(width=80, now=NOW)


def c(item_id: int, by: str, text: str, *children: Node, **extra: object) -> Node:
    item = Item(id=item_id, type="comment", by=by, time=NOW - 60, text=text)
    return Node(id=item_id, status=Status.OK, item=item, children=list(children), **extra)  # type: ignore[arg-type]


def story(*children: Node, text: str | None = None) -> Node:
    item = Item(
        id=1,
        type="story",
        by="op",
        time=NOW - 3600,
        title="Story",
        url="https://example.com",
        score=10,
        descendants=6,
        text=text,
    )
    return Node(id=1, status=Status.OK, item=item, children=list(children))


def example_tree() -> Node:
    return story(
        c(2, "alice", "Comment A", c(21, "bob", "Reply A1"), c(22, "carol", "Reply A2")),
        c(3, "dave", "Comment B", c(31, "erin", "Reply B1")),
        c(4, "frank", "Comment C"),
    )


def test_example_tree_layout() -> None:
    assert render_text(example_tree(), OPTS) == (
        "Story\n"
        "│ https://example.com\n"
        "│ ▲ 10 · op · 1 hour ago · 6 comments\n"
        "├── alice · 1 minute ago\n"
        "│   │ Comment A\n"
        "│   ├── bob · 1 minute ago\n"
        "│   │     Reply A1\n"
        "│   └── carol · 1 minute ago\n"
        "│         Reply A2\n"
        "├── dave · 1 minute ago\n"
        "│   │ Comment B\n"
        "│   └── erin · 1 minute ago\n"
        "│         Reply B1\n"
        "└── frank · 1 minute ago\n"
        "      Comment C\n"
    )


def test_wrapping_respects_width_and_keeps_rails() -> None:
    long = " ".join(["word"] * 60)
    tree = story(c(2, "alice", long, c(21, "bob", long)), c(3, "dave", long))
    lines = render_text(tree, RenderOptions(width=50, now=NOW)).splitlines()
    assert all(len(line) <= 50 for line in lines)
    alice = lines[
        lines.index("├── alice · 1 minute ago") + 1 : lines.index("│   └── bob · 1 minute ago")
    ]
    assert len(alice) > 1 and all(line.startswith("│   │ word") for line in alice)
    dave_body = lines[lines.index("└── dave · 1 minute ago") + 1 :]
    assert len(dave_body) > 1 and all(line.startswith("      word") for line in dave_body)


def test_paragraph_breaks_keep_the_rail_without_trailing_spaces() -> None:
    tree = story(c(2, "alice", "one<p>two", c(21, "bob", "x")))
    assert "    │ one\n    │\n    │ two\n" in render_text(tree, OPTS)


def test_story_body_and_code_blocks() -> None:
    tree = story(text="Intro<p><pre><code>  x = 1\n  y = 2</code></pre>")
    assert render_text(tree, OPTS) == (
        "Story\n"
        "  https://example.com\n"
        "  ▲ 10 · op · 1 hour ago · 6 comments\n"
        "\n"
        "  Intro\n"
        "\n"
        "    x = 1\n"
        "    y = 2\n"
    )


def test_placeholders() -> None:
    dead = Item(id=5, type="comment", by="troll", time=NOW, text="spam", dead=True)
    tree = story(
        Node(id=3, status=Status.DELETED, children=[c(31, "erin", "Reply")]),
        Node(id=5, status=Status.DEAD, item=dead),
        Node(id=8, status=Status.ERROR, error="HTTP 500"),
        Node.truncated(1),
        Node.truncated(4),
    )
    out = render_text(tree, OPTS)
    assert "├── [deleted]\n│   └── erin" in out
    assert "├── [flagged]\n" in out and "spam" not in out
    assert "├── [failed to load item 8: HTTP 500]\n" in out
    assert "├── … 1 more reply not shown\n" in out
    assert "└── … 4 more replies not shown\n" in out

    shown = render_text(tree, RenderOptions(width=80, now=NOW, show_dead=True))
    assert "├── troll · just now [flagged]\n│     spam" in shown


def test_color_only_when_enabled() -> None:
    assert "\x1b[" not in render_text(example_tree(), OPTS)
    assert "\x1b[36malice\x1b[0m" in render_text(
        example_tree(), RenderOptions(width=80, now=NOW, color=True)
    )


def test_tree_to_dict_preserves_structure() -> None:
    data = tree_to_dict(example_tree())
    assert data["title"] == "Story"
    assert [child["by"] for child in data["children"]] == ["alice", "dave", "frank"]
    assert [r["text"] for r in data["children"][0]["children"]] == ["Reply A1", "Reply A2"]
    assert data["children"][2]["children"] == []
