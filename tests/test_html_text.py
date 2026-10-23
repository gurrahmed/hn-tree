from __future__ import annotations

from hn_tree.html_text import Paragraph, html_to_paragraphs, html_to_text


def test_empty() -> None:
    assert html_to_paragraphs(None) == []
    assert html_to_paragraphs("") == []


def test_entities_are_decoded() -> None:
    assert html_to_text("it&#x27;s &amp; &lt;b&gt; &quot;q&quot; a&#x2F;b") == 'it\'s & <b> "q" a/b'


def test_paragraphs_split_on_p() -> None:
    assert html_to_text("one<p>two<p>three") == "one\n\ntwo\n\nthree"


def test_whitespace_is_collapsed() -> None:
    assert html_to_text("a\n  b\t c") == "a b c"


def test_link_with_url_as_text() -> None:
    html = (
        '<a href="https:&#x2F;&#x2F;x.com&#x2F;a" rel="nofollow">https:&#x2F;&#x2F;x.com&#x2F;a</a>'
    )
    assert html_to_text(html) == "https://x.com/a"


def test_link_with_shortened_text_shows_full_url() -> None:
    html = '<a href="https://example.com/very/long/path">https://example.com/very/lo...</a>'
    assert html_to_text(html) == "https://example.com/very/long/path"


def test_link_with_label() -> None:
    assert html_to_text('see <a href="https://x.com">docs</a>.') == "see docs (https://x.com)."


def test_italics() -> None:
    assert html_to_text("<i>really</i> good") == "*really* good"


def test_pre_block_keeps_layout() -> None:
    html = "Code:<p><pre><code>  def f():\n      return 1 &lt; 2\n</code></pre>\nAfter"
    assert html_to_paragraphs(html) == [
        Paragraph("Code:"),
        Paragraph("  def f():\n      return 1 < 2", preformatted=True),
        Paragraph("After"),
    ]


def test_malformed_html_does_not_raise() -> None:
    text = html_to_text("<i>unclosed <a href='x'>link<p>next</b></pre>")
    assert "unclosed" in text
    assert "next" in text
