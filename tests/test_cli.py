from __future__ import annotations

import argparse
import json

import pytest
import respx
from helpers import BASE_URL, SEARCH_URL, STORY, comment, serve, serve_null, serve_thread

from hn_tree.cli import main, parse_item_id

FLAGS = ["--base-url", BASE_URL, "--search-url", SEARCH_URL, "--width", "80", "--color", "never"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("50184820", 50184820),
        (" 42 ", 42),
        ("https://news.ycombinator.com/item?id=50184820", 50184820),
        ("http://news.ycombinator.com/item?id=7&p=2", 7),
    ],
)
def test_parse_item_id(value: str, expected: int) -> None:
    assert parse_item_id(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "0",
        "-1",
        "abc",
        "²",
        "https://example.com/item?id=1",
        "https://news.ycombinator.com/user?id=pg",
        "https://news.ycombinator.com/item?id=abc",
        "https://news.ycombinator.com/item",
    ],
)
def test_parse_item_id_rejects(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        parse_item_id(value)


def simple_thread(api: respx.MockRouter) -> None:
    serve(
        api,
        {**STORY, "kids": [2, 3]},
        comment(2, "alice", "Comment A", 21),
        comment(21, "bob", "Reply A1"),
        comment(3, "carol", "Comment B"),
    )


def test_item_renders_tree(api: respx.MockRouter, capsys: pytest.CaptureFixture[str]) -> None:
    simple_thread(api)
    assert main(["item", "https://news.ycombinator.com/item?id=1", *FLAGS]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "Story"
    assert out[3].startswith("├── alice · ")
    assert out[5].startswith("│   └── bob · ")
    assert out[7].startswith("└── carol · ")


def test_item_json(api: respx.MockRouter, capsys: pytest.CaptureFixture[str]) -> None:
    simple_thread(api)
    assert main(["item", "1", "--format", "json", *FLAGS]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [c["by"] for c in data["children"]] == ["alice", "carol"]
    assert data["children"][0]["children"][0]["text"] == "Reply A1"


def test_story_list_renders_independent_trees(
    api: respx.MockRouter, capsys: pytest.CaptureFixture[str]
) -> None:
    simple_thread(api)
    serve(api, {"id": 9, "type": "story", "title": "Second", "kids": []})
    api.get("topstories.json").respond(json=[1, 9, 10])
    assert main(["top", "--limit", "2", *FLAGS]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Story\n")
    assert "\n\nSecond\n" in out
    assert not any("item/10" in str(call.request.url) for call in api.calls)


def test_week_lists_search_results(
    api: respx.MockRouter, capsys: pytest.CaptureFixture[str]
) -> None:
    simple_thread(api)
    api.get("search").respond(json={"hits": [{"objectID": "1"}, {"objectID": "9"}]})
    assert main(["week", "--limit", "1", *FLAGS]) == 0
    assert capsys.readouterr().out.startswith("Story\n")


def test_missing_item_exits_1(
    api: respx.MockRouter, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    serve_null(api, 99)
    assert main(["item", "99", *FLAGS]) == 1
    assert capsys.readouterr().out == ""
    assert "item 99 does not exist" in caplog.text


def test_partial_failure_still_renders(
    api: respx.MockRouter,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    no_backoff: None,
) -> None:
    serve_thread(api)
    assert main(["item", "1", *FLAGS]) == 0
    assert "[failed to load item 8:" in capsys.readouterr().out
    assert "1 comment(s) could not be loaded" in caplog.text


def test_story_list_failure_exits_1(api: respx.MockRouter, no_backoff: None) -> None:
    api.get("newstories.json").respond(500)
    assert main(["new", *FLAGS]) == 1


def test_bad_usage_exits_2() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["item", "not-an-id"])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        main(["item", "1", "--max-depth", "-1"])
    assert exc.value.code == 2
