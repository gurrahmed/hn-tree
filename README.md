# hn-tree

Render a Hacker News story and its whole comment thread as a text tree, using the
official [Hacker News API](https://github.com/HackerNews/API).

```
Meta ordered to pay $25B penalty in New Mexico Cambridge Analytica case
│ https://www.cnbc.com/2026/10/21/meta-new-mexico-cambridge-analytica-penalty.html
│ ▲ 667 · cbg0 · 1 day ago · 408 comments
├── stillpointlab · 1 day ago
│   │ For those who haven't seen, the New Mexico Attorney General released tapes of the
│   │ depositions for Zuckerberg [1] and Sandberg [2].
│   ├── alpineman · 10 hours ago
│   │     30:30 mins
│   └── literallyroy · 20 hours ago
│       │ Thank you for the links!
│       └── … 2 more replies not shown
└── bananamogul · 1 day ago
      All the states that participated in the class action settlement got $725 million combined.
```

## Install

Requires Python 3.11+.

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'   # drop [dev] if you only want to run it
```

## Usage

```sh
hn-tree item 50184820                                         # one story by id
hn-tree item 'https://news.ycombinator.com/item?id=50184820'  # or by URL
hn-tree item 50184820 --max-depth 2                           # top comments + one level of replies
hn-tree top --limit 3                                         # three separate trees
hn-tree ask --limit 5 --format json                           # also new, best, show, job
hn-tree week --limit 5                                        # most upvoted stories of the past 7 days
```

| Flag | Meaning |
|---|---|
| `--max-depth N` | Reply levels to fetch below the story. `0` fetches the story only. |
| `--max-nodes N` | Maximum comments fetched per story. |
| `--show-dead` | Keep deleted, flagged and missing comments, and show flagged text. |
| `--format text\|json` | A box-drawing tree (the default), or nested JSON with comment HTML converted to plain text. |
| `--width COLS` | Wrap width. Defaults to the terminal width, or 100 when piped. |
| `--color auto\|always\|never`, `--no-color` | ANSI colors. `auto` uses them only on a terminal, and honours `NO_COLOR`. |
| `--concurrency N` | Requests in flight (default 8). |
| `--timeout SECONDS` | Timeout per request (default 10). |
| `-v`, `-vv` | Log progress to stderr. `-vv` logs every request. |

### Web feed

```sh
hn-tree serve                       # this week's 5 most upvoted stories, on http://127.0.0.1:8000/
hn-tree serve --feed new --limit 10 --interval 30 --port 9000
```

A page listing stories (by default `--feed week`) that re-checks the API every `--interval`
seconds (default 15) and highlights stories that appeared since the last check. Clicking a title opens that story's
comment thread, with collapsible replies. `--max-depth`, `--max-nodes`, `--show-dead` and the
network flags apply to the threads it loads. Polling pauses while the tab is in the background,
and the Pause button stops it. The server listens on `127.0.0.1` only unless you pass `--host`.

Exit codes:

| Code | Meaning |
|---|---|
| `0` | Success, including when some comments failed to load. Those are shown inline as `[failed to load …]`, with a warning on stderr. |
| `1` | A story itself, or the story list, could not be fetched. For `serve`: the port could not be opened. |
| `2` | Bad usage. |
| `130` | Interrupted with Ctrl-C. |
| `141` | Stdout was closed early, for example by `\| head`. |

## How it works

| Module | Responsibility |
|---|---|
| `client.py` | One pooled `httpx.AsyncClient`. A semaphore caps requests in flight. Network errors, 429 and 5xx responses are retried with exponential backoff and jitter (and `Retry-After` is honoured). Results are cached in memory per run. The `week` list comes from [hn.algolia.com's search API](https://hn.algolia.com/api), because the official API can't filter by date. The stories themselves still load from the official API. |
| `builder.py` | Fetches the thread **one level at a time**, with every item on a level requested concurrently. Children keep the order of the parent's `kids` list, which is HN's ranked order. Like the site, it drops deleted or flagged comments that have no replies, and keeps the ones that do as `[deleted]` / `[flagged]` placeholders. If the root loads but a comment below it fails, that comment becomes an error node and the rest of the tree still renders. |
| `html_text.py` | Converts HN's HTML (`<p>`, `<a>`, `<i>`, `<pre><code>`, entities) to paragraphs. Code blocks are never re-wrapped. |
| `web.py` | `hn-tree serve`: a stdlib threaded HTTP server with `/api/feed` and `/api/item/<id>` (the same JSON as `--format json`), plus the page in `static/index.html`. Each request runs its own event loop and client, so a reload always fetches fresh data. |
| `render.py` | A pure function from the tree to text. Each child's prefix is its parent's prefix plus `│   ` (or four spaces after a last child). Wrapped lines carry the rail down to the node's own replies. |

Building the tree and rendering it are both iterative, so deep threads can't hit Python's recursion limit.

**Performance:** the API returns one item per request, so a 400-comment thread takes about 400 requests. Firebase serves HTTP/1.1 only, and opening more than about 8 parallel connections made it *slower* in testing. With the defaults, that thread takes about 5–6s.

## Development

```sh
.venv/bin/pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy
```

The tests mock the API with `respx`, so they make no network calls.
