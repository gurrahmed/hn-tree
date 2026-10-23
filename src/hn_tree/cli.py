"""Command-line entry point: ``hn-tree item <id-or-url>``, ``hn-tree top``, ``hn-tree serve``."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import shutil
import sys
import time
from collections.abc import Callable, Sequence
from typing import Final
from urllib.parse import parse_qs, urlparse

from . import __version__, web
from .builder import build_tree
from .client import DEFAULT_BASE_URL, DEFAULT_SEARCH_URL, STORY_LISTS, HNClient, HNError
from .models import Node, Status
from .render import RenderOptions, render_text, tree_to_dict

log = logging.getLogger("hn_tree")

EXIT_OK: Final = 0
EXIT_FETCH_FAILED: Final = 1
EXIT_LISTEN_FAILED: Final = 1  # serve could not open its port
EXIT_USAGE: Final = 2  # raised by argparse itself
EXIT_INTERRUPTED: Final = 130
EXIT_BROKEN_PIPE: Final = 141

_LIST_HELP: Final = {"week": "the most upvoted stories of the past seven days"}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose)
    if args.command == "serve":
        return _serve(args)
    try:
        code, trees = asyncio.run(_fetch(args))
        if trees:
            sys.stdout.write(_format(trees, args))
            sys.stdout.flush()
    except KeyboardInterrupt:
        print("hn-tree: interrupted", file=sys.stderr)
        return EXIT_INTERRUPTED
    except BrokenPipeError:
        # The reader went away (e.g. `| head`). Point stdout at devnull so the
        # interpreter's final flush doesn't raise a second time.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return EXIT_BROKEN_PIPE
    return code


def build_parser() -> argparse.ArgumentParser:
    tree_options = argparse.ArgumentParser(add_help=False)
    tree = tree_options.add_argument_group("tree")
    tree.add_argument(
        "--max-depth",
        type=_int_at_least(0),
        metavar="N",
        help="reply levels to fetch below the story (0 = story only)",
    )
    tree.add_argument(
        "--max-nodes",
        type=_int_at_least(1),
        metavar="N",
        help="maximum comments to fetch per story",
    )
    tree.add_argument(
        "--show-dead",
        action="store_true",
        help="keep deleted, flagged and missing comments, and show flagged text",
    )
    output_options = argparse.ArgumentParser(add_help=False)
    output = output_options.add_argument_group("output")
    output.add_argument("--format", choices=("text", "json"), default="text")
    output.add_argument(
        "--width",
        type=_int_at_least(40),
        metavar="COLS",
        help="wrap width (default: terminal width, or 100 when piped)",
    )
    output.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="ANSI colors (auto: only on a terminal, honouring NO_COLOR)",
    )
    output.add_argument(
        "--no-color",
        dest="color",
        action="store_const",
        const="never",
        help="same as --color never",
    )
    common = argparse.ArgumentParser(add_help=False)
    network = common.add_argument_group("network")
    network.add_argument(
        "--concurrency",
        type=_int_at_least(1),
        default=8,
        metavar="N",
        help="maximum requests in flight (default: 8; more is slower against Firebase)",
    )
    network.add_argument(
        "--timeout",
        type=_positive_float,
        default=10.0,
        metavar="SECONDS",
        help="per-request timeout (default: 10)",
    )
    network.add_argument(
        "--base-url", default=DEFAULT_BASE_URL, help="API root (default: %(default)s)"
    )
    network.add_argument(
        "--search-url",
        default=DEFAULT_SEARCH_URL,
        help="search API used by the week list (default: %(default)s)",
    )
    common.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="log progress to stderr (-vv for every request)",
    )

    parser = argparse.ArgumentParser(
        prog="hn-tree",
        description="Render Hacker News stories and their comment threads as text trees.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    item = commands.add_parser(
        "item",
        parents=[tree_options, output_options, common],
        help="one story (or comment) and its replies",
    )
    item.add_argument(
        "item_id",
        type=parse_item_id,
        metavar="ID_OR_URL",
        help="an item id or a news.ycombinator.com/item?id=... URL",
    )
    for kind in STORY_LISTS:
        stories = commands.add_parser(
            kind,
            parents=[tree_options, output_options, common],
            help=_LIST_HELP.get(kind, f"the current {kind} stories"),
        )
        stories.add_argument(
            "--limit",
            type=_int_at_least(1),
            default=5,
            metavar="N",
            help="how many stories to render (default: 5)",
        )
    serve = commands.add_parser(
        "serve",
        parents=[tree_options, common],
        help="a web page of stories that keeps itself up to date",
    )
    serve.add_argument(
        "--feed",
        choices=STORY_LISTS,
        default="week",
        help="which story list to show (default: week)",
    )
    serve.add_argument(
        "--limit",
        type=_int_at_least(1),
        default=5,
        metavar="N",
        help="how many stories to list (default: 5)",
    )
    serve.add_argument(
        "--interval",
        type=_int_at_least(5),
        default=15,
        metavar="SECONDS",
        help="how often the page checks for new stories (default: 15)",
    )
    serve.add_argument(
        "--host", default="127.0.0.1", help="address to listen on (default: %(default)s)"
    )
    serve.add_argument(
        "--port",
        type=_int_at_least(0, maximum=65535),
        default=8000,
        help="port to listen on; 0 picks a free one (default: %(default)s)",
    )
    return parser


def parse_item_id(value: str) -> int:
    """Accept ``50184820`` or ``https://news.ycombinator.com/item?id=50184820``."""
    value = value.strip()
    if value.isascii() and value.isdigit():
        item_id = int(value)
    else:
        url = urlparse(value)
        if (
            url.scheme not in ("http", "https")
            or url.hostname != "news.ycombinator.com"
            or url.path != "/item"
        ):
            raise argparse.ArgumentTypeError(
                f"expected an item id or a news.ycombinator.com/item?id=... URL, got {value!r}"
            )
        ids = parse_qs(url.query).get("id", [])
        if len(ids) != 1 or not (ids[0].isascii() and ids[0].isdigit()):
            raise argparse.ArgumentTypeError(f"URL has no valid ?id= parameter: {value!r}")
        item_id = int(ids[0])
    if item_id <= 0:
        raise argparse.ArgumentTypeError("item ids are positive integers")
    return item_id


async def _fetch(args: argparse.Namespace) -> tuple[int, list[Node]]:
    started = time.monotonic()
    client = HNClient(
        args.base_url,
        search_url=args.search_url,
        concurrency=args.concurrency,
        timeout=args.timeout,
    )
    async with client:
        if args.command == "item":
            ids = [args.item_id]
        else:
            try:
                ids = (await client.get_story_ids(args.command))[: args.limit]
            except HNError as exc:
                log.error("could not load %s stories: %s", args.command, exc)
                return EXIT_FETCH_FAILED, []

        # Each story is its own tree; they share the client, its cache and its request cap.
        results = await asyncio.gather(
            *(
                build_tree(
                    client,
                    item_id,
                    max_depth=args.max_depth,
                    max_nodes=args.max_nodes,
                    show_dead=args.show_dead,
                )
                for item_id in ids
            ),
            return_exceptions=True,
        )

    trees: list[Node] = []
    code = EXIT_OK
    for item_id, result in zip(ids, results, strict=True):
        if isinstance(result, HNError):
            log.error("could not load item %d: %s", item_id, result)
            code = EXIT_FETCH_FAILED
        elif isinstance(result, BaseException):
            raise result
        else:
            trees.append(result)

    failed = sum(1 for tree in trees for node in tree.walk() if node.status is Status.ERROR)
    if failed:
        log.warning("%d comment(s) could not be loaded; shown as [failed to load ...]", failed)
    log.info("built %d tree(s) in %.1fs", len(trees), time.monotonic() - started)
    return code, trees


def _serve(args: argparse.Namespace) -> int:
    settings = web.Settings(
        base_url=args.base_url,
        search_url=args.search_url,
        feed=args.feed,
        limit=args.limit,
        interval=args.interval,
        max_depth=args.max_depth,
        max_nodes=args.max_nodes,
        show_dead=args.show_dead,
        concurrency=args.concurrency,
        timeout=args.timeout,
    )
    try:
        server = web.make_server(settings, args.host, args.port)
    except OSError as exc:
        log.error("could not listen on %s:%d: %s", args.host, args.port, exc)
        return EXIT_LISTEN_FAILED
    with server:
        print(
            f"hn-tree: serving http://{args.host}:{server.server_port}/ (Ctrl-C to stop)",
            file=sys.stderr,
        )
        with contextlib.suppress(KeyboardInterrupt):  # Ctrl-C is the normal way to stop
            server.serve_forever()
    return EXIT_OK


def _format(trees: list[Node], args: argparse.Namespace) -> str:
    if args.format == "json":
        if args.command == "item":
            return json.dumps(tree_to_dict(trees[0]), ensure_ascii=False, indent=2) + "\n"
        return json.dumps([tree_to_dict(t) for t in trees], ensure_ascii=False, indent=2) + "\n"
    options = RenderOptions(
        width=args.width or shutil.get_terminal_size((100, 24)).columns,
        color=_use_color(args.color),
        show_dead=args.show_dead,
    )
    return "\n".join(render_text(tree, options) for tree in trees)


def _use_color(choice: str) -> bool:
    if choice == "auto":
        return sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    return choice == "always"


def _configure_logging(verbosity: int) -> None:
    if verbosity == 0:
        level = logging.WARNING
    elif verbosity == 1:
        level = logging.INFO
    else:
        level = logging.DEBUG
    logging.basicConfig(
        level=level, format="hn-tree: %(levelname)s: %(message)s", stream=sys.stderr
    )
    # httpx logs every request at INFO; only show that at -vv.
    logging.getLogger("httpx").setLevel(logging.DEBUG if verbosity >= 2 else logging.WARNING)


def _int_at_least(minimum: int, *, maximum: int | None = None) -> Callable[[str], int]:
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"expected an integer, got {value!r}") from None
        if number < minimum:
            raise argparse.ArgumentTypeError(f"must be at least {minimum}")
        if maximum is not None and number > maximum:
            raise argparse.ArgumentTypeError(f"must be at most {maximum}")
        return number

    return parse


def _positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number, got {value!r}") from None
    if not number > 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return number
