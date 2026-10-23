"""Convert the HTML stored in HN ``text`` fields into plain-text paragraphs.

HN uses a small subset of HTML: ``<p>`` between paragraphs (never closed), ``<a>``,
``<i>``, ``<pre><code>`` for code, and HTML entities such as ``&#x27;`` and ``&#x2F;``.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser


@dataclass(frozen=True, slots=True)
class Paragraph:
    text: str
    preformatted: bool = False  # code block: keep line breaks and indentation, never re-wrap


def html_to_paragraphs(html: str | None) -> list[Paragraph]:
    if not html:
        return []
    parser = _HNTextParser()
    parser.feed(html)
    parser.close()
    return parser.paragraphs


def html_to_text(html: str | None) -> str:
    return "\n\n".join(p.text for p in html_to_paragraphs(html))


class _HNTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)  # decodes entities in text and attributes
        self.paragraphs: list[Paragraph] = []
        self._buf: list[str] = []
        self._pre_depth = 0
        # (href, index in _buf where the link's text starts)
        self._open_links: list[tuple[str | None, int]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "p":
            self._flush()
        elif tag == "pre":
            self._flush()
            self._pre_depth += 1
        elif tag in ("i", "em") and not self._pre_depth:
            self._buf.append("*")
        elif tag == "a":
            self._open_links.append((dict(attrs).get("href"), len(self._buf)))

    def handle_endtag(self, tag: str) -> None:
        if tag == "pre" and self._pre_depth:
            self._flush()
            self._pre_depth -= 1
        elif tag in ("i", "em") and not self._pre_depth:
            self._buf.append("*")
        elif tag == "a" and self._open_links:
            href, start = self._open_links.pop()
            self._buf[start:] = [_format_link("".join(self._buf[start:]), href)]

    def handle_data(self, data: str) -> None:
        self._buf.append(data)

    def close(self) -> None:
        super().close()
        self._flush()

    def _flush(self) -> None:
        raw = "".join(self._buf)
        self._buf.clear()
        self._open_links.clear()  # a link left open across a paragraph keeps its text as-is
        if self._pre_depth:
            lines = [line.rstrip() for line in raw.expandtabs(4).split("\n")]
            while lines and not lines[0]:
                lines.pop(0)
            while lines and not lines[-1]:
                lines.pop()
            text = "\n".join(lines)
        else:
            text = " ".join(raw.split())
        if text:
            self.paragraphs.append(Paragraph(text, preformatted=bool(self._pre_depth)))


def _format_link(label: str, href: str | None) -> str:
    label = label.strip()
    if not href:
        return label
    if not label or label == href:
        return href
    # HN shortens long URLs in the visible text, e.g. "https://example.com/a/very/lo...".
    if label.endswith("...") and href.startswith(label[:-3]):
        return href
    return f"{label} ({href})"
