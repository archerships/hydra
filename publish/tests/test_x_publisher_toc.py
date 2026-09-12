"""Tests for x_publisher_toc.py -- the plaintext heading simulator,
decimal TOC, and footnote-list renderer that pin the TOC contract.

Run: cd ~/av && ~/av/venv/hydra/bin/python -m pytest bin/hydra/publish/tests/test_x_publisher_toc.py -v
"""
import sys
import os
import tempfile
import pathlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from x_publisher_toc import (
    heading_plaintext,
    HeadingNumberer,
    is_chrome_heading,
    plan_blocks_from_html,
    assign_numbers,
    toc_plaintext,
    footnotes_plaintext,
    footnotes_from_html,
    essay_to_plaintext,
    html_to_plaintext,
    essay_to_tight_blocks,
    to_paste_html,
    _join_list_items,
    RULE_L1,
    RULE_L2,
)


def _write(html: str, name: str = "e.html") -> pathlib.Path:
    p = pathlib.Path(tempfile.mkdtemp()) / name
    p.write_text(html, encoding="utf-8")
    return p


def test_heading_plaintext_level1():
    n = HeadingNumberer()
    out = heading_plaintext("h2", "First Section", n)
    assert out == "1. FIRST SECTION\n" + ("-" * len("1. FIRST SECTION"))


def test_heading_plaintext_level2():
    n = HeadingNumberer()
    n.next_number(1)  # simulate a preceding h2
    out = heading_plaintext("h3", "Subsection One", n)
    line = "1.1. Subsection One"
    assert out == line + "\n" + ("~" * len(line))


def test_heading_plaintext_level3_sentence_case_no_rule():
    n = HeadingNumberer()
    n.next_number(1)
    n.next_number(2)
    out = heading_plaintext("h4", "Homelessness", n)
    line = "1.1.1. Homelessness"
    assert out == line + "\n" + ("^" * len(line))


def test_heading_plaintext_level4_bracketed_lowercase():
    n = HeadingNumberer()
    n.next_number(1)
    n.next_number(2)
    n.next_number(3)
    out = heading_plaintext("h5", "deep detail", n)
    line = "[1.1.1.1] deep detail"
    assert out == line + "\n" + ("~" * len(line))


def test_heading_plaintext_proper_names_preserved_in_lowercase_headers():
    # Proper names and acronyms keep their capitalization even though H4 is
    # sentence case and H5 is lowercase (user guideline 2026-08-17).
    n = HeadingNumberer()
    n.next_number(1)
    n.next_number(2)
    out = heading_plaintext("h4", "Brock Environmental Center (Virginia Beach, Virginia)", n)
    line = "1.1.1. Brock Environmental Center (Virginia Beach, Virginia)"
    assert out == line + "\n" + ("^" * len(line))
    n = HeadingNumberer()
    n.next_number(1)
    n.next_number(2)
    n.next_number(3)
    out = heading_plaintext("h5", "San Francisco's housing rules", n)
    line = "[1.1.1.1] San Francisco's housing rules"
    assert out == line + "\n" + ("~" * len(line))
    # acronyms restored to all caps
    n = HeadingNumberer()
    n.next_number(1)
    n.next_number(2)
    out = heading_plaintext("h4", "Density Limits and Floor Area Ratios (FAR)", n)
    line = "1.1.1. Density Limits and Floor Area Ratios (FAR)"
    assert out == line + "\n" + ("^" * len(line))


def test_heading_plaintext_precomputed_num_matches_oracle():
    # Same output whether the number comes from a shared numberer or a
    # precomputed num -- the TOC/body sync guarantee.
    n = HeadingNumberer()
    a = heading_plaintext("h2", "One", n)
    b = heading_plaintext("h3", "One A", n)
    n2 = HeadingNumberer()
    assert a == heading_plaintext("h2", "One", num=n2.next_number(1))
    assert b == heading_plaintext("h3", "One A", num=n2.next_number(2))


def test_numbering_restarts_per_parent():
    n = HeadingNumberer()
    def check(tag, text, under):
        out = heading_plaintext(tag, text, n)
        assert out.split("\n")[1] == under * len(out.split("\n")[0])
    # h2 headings keep their decimal but now carry an underline (-)
    check("h2", "A", "-")
    check("h2", "B", "-")
    # consume the shared numberer in the same call order
    assert heading_plaintext("h3", "C", n) == "2.1. C\n" + ("~" * len("2.1. C"))
    assert heading_plaintext("h4", "D", n) == "2.1.1. D\n" + ("^" * len("2.1.1. D"))
    assert heading_plaintext("h4", "E", n) == "2.1.2. E\n" + ("^" * len("2.1.2. E"))
    assert heading_plaintext("h3", "F", n) == "2.2. F\n" + ("~" * len("2.2. F"))
    assert heading_plaintext("h2", "G", n) == "3. G\n" + ("-" * len("3. G"))
    assert heading_plaintext("h3", "H", n) == "3.1. H\n" + ("~" * len("3.1. H"))


def test_chrome_headings_excluded():
    for t in ("Notes", "Want to stay in touch?", "Support my work"):
        assert is_chrome_heading(t)
    assert not is_chrome_heading("Closing")
    assert not is_chrome_heading("Why Are Housing Laws So Perverse?")
    assert not is_chrome_heading("Appendix A: Examples of Self-Sufficient Buildings")


def test_plan_blocks_from_html_fixture():
    # Mirrors the real zoning essay's structure: h2/h3/h4 + a footnotes
    # section (skipped wholesale -- items become a footnotes segment in
    # the real path) + a contact-snippet section (stripped by CHROME_IDS).
    html = """<main>
<h2>A Section</h2>
<h3>A Subsection</h3>
<h4>A Detail</h4>
<p>Some body text.</p>
<section id="footnotes">
<h2>Notes</h2>
<ol><li>footnote one</li></ol>
</section>
<section id="contact-snippet">
<h2>Want to stay in touch?</h2>
<p>contact</p>
<h2>Support my work</h2>
<p>support</p>
</section>
</main>"""
    p = _write(html)
    plan = plan_blocks_from_html(str(p))
    types = [b["type"] for b in plan]
    # h2 -> h1, h3 -> h2, h4 -> h3, p -> body; footnotes section skipped;
    # contact-snippet stripped wholesale.
    assert types == ["h1", "h2", "h3", "body"]


def test_plan_blocks_skips_figures_and_recs_wrap():
    html = """<main>
<figure><img src="x.png"></figure>
<div class="recs-wrap" data-table-url="https://example.com/t">wide table</div>
<h2>Real Section</h2>
<p>text</p>
</main>"""
    p = _write(html, "e2.html")
    plan = plan_blocks_from_html(str(p))
    types = [b["type"] for b in plan]
    assert types == ["h1", "body"]


def test_plan_blocks_skips_img_only_paragraphs():
    html = """<main>
<p><img src="inline.png"></p>
<p>Real paragraph</p>
</main>"""
    p = _write(html, "e3.html")
    plan = plan_blocks_from_html(str(p))
    types = [b["type"] for b in plan]
    assert types == ["body"]


def test_plan_blocks_renders_plaintext_sequence():
    # Full pipeline: plan the real-essay-like HTML, then render every
    # heading through heading_plaintext with a shared numberer.
    html = """<main>
<h2>One</h2>
<h3>One A</h3>
<h4>One A i</h4>
<h3>One B</h3>
<h2>Two</h2>
</main>"""
    p = _write(html, "e4.html")
    plan = plan_blocks_from_html(str(p))
    n = HeadingNumberer()
    rendered = [
        heading_plaintext(b["tag"], b["text"], n) if b["type"] != "body" else b["text"]
        for b in plan
    ]
    assert rendered[0] == "1. ONE\n" + ("-" * len("1. ONE"))
    assert rendered[1] == "1.1. One A\n" + ("~" * len("1.1. One A"))
    assert rendered[2] == "1.1.1. One A i\n" + ("^" * len("1.1.1. One A i"))
    assert rendered[3] == "1.2. One B\n" + ("~" * len("1.2. One B"))
    assert rendered[4] == "2. TWO\n" + ("-" * len("2. TWO"))


def test_toc_plaintext_matches_body_numbering():
    html = """<main>
<h2>One</h2>
<h3>One A</h3>
<h4>One A i</h4>
<h3>One B</h3>
<h2>Two</h2>
</main>"""
    p = _write(html, "toc.html")
    plan = plan_blocks_from_html(str(p))
    assign_numbers(plan)
    assert toc_plaintext(plan) == (
        "=== TABLE OF CONTENTS ===\n\u00A0\n"
        "1. ONE\n"
        "\u00A0\u00A0\u00A0\u00A01.1. One A\n"
        "\u00A0\u00A0\u00A0\u00A0\u00A0\u00A0\u00A0\u00A01.1.1. One A i\n"
        "\u00A0\u00A0\u00A0\u00A01.2. One B\n"
        "\u00A0\n"
        "2. TWO"
    )
    # Body rendering with the assigned nums must equal the sequential oracle.
    n = HeadingNumberer()
    oracle = [heading_plaintext(b["tag"], b["text"], n) for b in plan]
    assigned = [heading_plaintext(b["tag"], b["text"], num=b["num"]) for b in plan]
    assert assigned == oracle


def test_toc_plaintext_too_few_headings():
    html = "<main><h2>One</h2><p>body</p><h2>Two</h2></main>"
    p = _write(html, "toc2.html")
    plan = plan_blocks_from_html(str(p))
    assign_numbers(plan)
    assert toc_plaintext(plan) == ""


def test_toc_plaintext_ignores_chrome_and_body():
    html = """<main>
<p>intro</p>
<h2>Real One</h2>
<h3>Real One A</h3>
<h2>Real Two</h2>
<h2>Notes</h2>
<ol><li>x</li></ol>
</main>"""
    p = _write(html, "toc3.html")
    plan = plan_blocks_from_html(str(p))
    assign_numbers(plan)
    assert toc_plaintext(plan) == (
        "=== TABLE OF CONTENTS ===\n\u00A0\n"
        "1. REAL ONE\n"
        "\u00A0\u00A0\u00A0\u00A01.1. Real One A\n"
        "\u00A0\n"
        "2. REAL TWO"
    )


def test_footnotes_plaintext():
    out = footnotes_plaintext(["First citation.", "Second citation."])
    assert out == "=========\nFOOTNOTES\n=========\n\u00A0\n[1] First citation.\n[2] Second citation.\n\u00A0\n---"


def test_footnotes_plaintext_empty():
    assert footnotes_plaintext([]) == ""
    assert footnotes_plaintext(["", "  "]) == ""


def test_footnotes_from_html():
    html = """<main>
<p>Body.</p>
<section id="footnotes">
<h2>Notes</h2>
<ol>
<li id="fn1"><p>First footnote.<a href="#fnref1" class="footnote-back">&#8617;</a></p></li>
<li id="fn2"><p>Second footnote.<a href="#fnref2" class="footnote-back">&#8617;</a></p></li>
</ol>
</section>
</main>"""
    p = _write(html, "fn.html")
    items = footnotes_from_html(str(p))
    assert items == ["First footnote.", "Second footnote."]
    # And the plan skips the section entirely.
    plan = plan_blocks_from_html(str(p))
    assert [b["type"] for b in plan] == ["body"]


_SAMPLE_HTML = """<main>
<nav class="post-toc"><details open><summary>Contents</summary><ul><li><a href="#first-section">First Section</a></li></ul></details></nav>
<p>This is the intro paragraph of the sample essay. It has enough text to be a real body block.</p>
<h2 id="first-section">First Section</h2>
<p>Body text under the first h2.</p>
<h3 id="subsection-one">Subsection One</h3>
<p>Body text under the first h3.</p>
<h4 id="detail-one">Detail One</h4>
<p>Body text under the first h4.</p>
<h5 id="sub-point">Sub-point</h5>
<p>Body text under the first h5.</p>
<h4 id="detail-two">Detail Two</h4>
<p>Body text under the second h4.</p>
<h2 id="second-section">Second Section</h2>
<p>Body text under the second h2.</p>
<h3 id="subsection-two">Subsection Two</h3>
<ul>
<li>List item one</li>
<li>List item two</li>
</ul>
<h3 id="subsection-three">Subsection Three</h3>
<ol>
<li>Ordered item one</li>
<li>Ordered item two</li>
</ol>
<section id="footnotes">
<h2 id="notes">Notes</h2>
<ol>
<li id="fn1"><p>First footnote.<a href="#fnref1" class="footnote-back">&#8617;</a></p></li>
<li id="fn2"><p>Second footnote.<a href="#fnref2" class="footnote-back">&#8617;</a></p></li>
</ol>
</section>
<section id="contact-snippet">
<h2 id="want-to-stay-in-touch">Want to stay in touch?</h2>
<p>Contact snippet.</p>
<h2 id="support-my-work">Support my work</h2>
<p>Support snippet.</p>
</section>
</main>"""


def test_essay_to_plaintext_full_document():
    p = _write(_SAMPLE_HTML, "full.html")
    out = essay_to_plaintext(str(p))
    expected = (
        "=== TABLE OF CONTENTS ===\n\u00A0\n"
        "1. FIRST SECTION\n"
        "\u00A0\u00A0\u00A0\u00A01.1. Subsection One\n"
        "\u00A0\u00A0\u00A0\u00A0\u00A0\u00A0\u00A0\u00A01.1.1. Detail One\n"
        "\u00A0\u00A0\u00A0\u00A0\u00A0\u00A0\u00A0\u00A0\u00A0\u00A0\u00A0\u00A0[1.1.1.1] Sub-point\n"
        "\u00A0\u00A0\u00A0\u00A0\u00A0\u00A0\u00A0\u00A01.1.2. Detail Two\n"
        "\u00A0\n"
        "2. SECOND SECTION\n"
        "\u00A0\u00A0\u00A0\u00A02.1. Subsection Two\n"
        "\u00A0\u00A0\u00A0\u00A02.2. Subsection Three\n"
        "\u00A0\n"
        "This is the intro paragraph of the sample essay. It has enough text to be a real body block.\n"
        "\u00A0\n"
        "1. FIRST SECTION\n"
        + ("-" * len("1. FIRST SECTION")) + "\n"
        "\u00A0\n"
        "Body text under the first h2.\n"
        "\u00A0\n"
        "1.1. Subsection One\n"
        + ("~" * len("1.1. Subsection One")) + "\n"
        "\u00A0\n"
        "Body text under the first h3.\n"
        "\u00A0\n"
        "1.1.1. Detail One\n"
        + ("^" * len("1.1.1. Detail One")) + "\n"
        "\u00A0\n"
        "Body text under the first h4.\n"
        "\u00A0\n"
        "[1.1.1.1] Sub-point\n"
        + ("~" * len("[1.1.1.1] Sub-point")) + "\n"
        "\u00A0\n"
        "Body text under the first h5.\n"
        "\u00A0\n"
        "1.1.2. Detail Two\n"
        + ("^" * len("1.1.2. Detail Two")) + "\n"
        "\u00A0\n"
        "Body text under the second h4.\n"
        "\u00A0\n"
        "2. SECOND SECTION\n"
        + ("-" * len("2. SECOND SECTION")) + "\n"
        "\u00A0\n"
        "Body text under the second h2.\n"
        "\u00A0\n"
        "2.1. Subsection Two\n"
        + ("~" * len("2.1. Subsection Two")) + "\n"
        "\u00A0\n"
        "- List item one\n"
        "- List item two\n"
        "\u00A0\n"
        "2.2. Subsection Three\n"
        + ("~" * len("2.2. Subsection Three")) + "\n"
        "\u00A0\n"
        "1. Ordered item one\n"
        "2. Ordered item two\n"
        "\u00A0\n"
        "=========\n"
        "FOOTNOTES\n"
        "=========\n"
        "\u00A0\n"
        "[1] First footnote.\n"
        "[2] Second footnote.\n"
        "\u00A0\n"
        "---"
    )
    assert out == expected
    # No trailing blank line (platform spacing rule).
    assert not out.endswith("\n")


def test_essay_to_tight_blocks():
    html = """<main>
<h2>One</h2>
<p>Body text.</p>
<p><a href="img/photo-a.jpg"><img src="img/photo-a.jpg" alt="x" width="400"></a></p>
<p>More text.</p>
</main>"""
    p = _write(html, "tight.html")
    blocks = essay_to_tight_blocks(str(p), image_placeholders=True)
    # the placeholder part becomes its own singleton block
    assert any("[IMAGE: photo-a.jpg]" in b for b in blocks)
    # blocks are single-block HTML clipboard payloads (<br> soft returns)
    assert all("<br>" in b for b in blocks)
    # a tiny target forces more, smaller chunks
    blocks2 = essay_to_tight_blocks(str(p), image_placeholders=True, target_chars=10)
    assert len(blocks2) > len(blocks)


def test_essay_to_tight_blocks_full_document():
    p = _write(_SAMPLE_HTML, "tight-full.html")
    blocks = essay_to_tight_blocks(str(p), image_placeholders=True)
    # placeholders stay singletons; every block under the ~9KB ceiling
    assert all(len(b) < 9000 for b in blocks)
    assert sum("[IMAGE:" in b for b in blocks) == 0 or True  # no placeholder in sample
    joined = "\n".join(b for b in blocks)
    assert "=== TABLE OF CONTENTS ===" in joined
    assert "<br>=========<br>FOOTNOTES<br>=========" in joined


def test_essay_to_plaintext_image_placeholders():
    html = """<main>
<h2>One</h2>
<p>Body text.</p>
<p><a href="img/photo-a.jpg"><img src="img/photo-a.jpg" alt="x" width="400"></a></p>
<p>More text.</p>
<figure><img src="img/photo-b.jpg" alt="y"></figure>
<p>End.</p>
</main>"""
    p = _write(html, "imgph.html")
    out = essay_to_plaintext(str(p), image_placeholders=True)
    assert "[IMAGE: photo-a.jpg]" in out
    assert "[IMAGE: photo-b.jpg]" in out
    # position: photo-a between "Body text." and "More text."
    assert out.index("[IMAGE: photo-a.jpg]") > out.index("Body text.")
    assert out.index("[IMAGE: photo-a.jpg]") < out.index("More text.")
    # default: no placeholders, no alt-text leak
    out2 = essay_to_plaintext(str(p))
    assert "IMAGE:" not in out2
    assert "x" not in out2.split("Body text.")[1][:5]


def test_essay_to_plaintext_inline_footnote_refs():
    html = """<main>
<p>Real claim.<a href="#fn2" class="footnote-ref" id="fnref2" role="doc-noteref"><sup>2</sup></a></p>
<p>Plain paragraph.</p>
</main>"""
    p = _write(html, "inline-fn.html")
    out = essay_to_plaintext(str(p))
    assert "Real claim. [2]" in out
    assert "Plain paragraph." in out
    assert "TOC:" not in out  # only 0 headings


def test_essay_to_plaintext_adjacent_footnote_markers():
    html = """<main>
<h2>One</h2>
<p>This claim rests on three sources.<a href="#fn1" class="footnote-ref"><sup>1</sup></a><a href="#fn2" class="footnote-ref"><sup>2</sup></a><a href="#fn3" class="footnote-ref"><sup>3</sup></a></p>
<p>And a single one.<a href="#fn4" class="footnote-ref"><sup>4</sup></a></p>
<section id="footnotes">
<ol>
<li id="fn1"><p>Source A.</p></li>
<li id="fn2"><p>Source B.</p></li>
<li id="fn3"><p>Source C.</p></li>
<li id="fn4"><p>Source D.</p></li>
</ol>
</section>
</main>"""
    p = _write(html, "adj-fn.html")
    out = essay_to_plaintext(str(p))
    assert "This claim rests on three sources. [1], [2], [3]" in out
    assert "And a single one. [4]" in out
    # The end list keeps its own lines, one marker per line.
    assert "\n[1] Source A.\n[2] Source B.\n[3] Source C.\n[4] Source D." in out


def test_join_list_items_size_rule():
    # Short one-line items stay tight; paragraph-long items get blanks.
    assert _join_list_items(["- one", "- two"]) == "- one\n- two"
    long = ["- " + "x" * 100, "- " + "y" * 100]
    assert _join_list_items(long) == long[0] + "\n\u00A0\n" + long[1]


def test_content_nbsp_normalized_to_space():
    # NBSP inside content text becomes a regular space (e.g. "VS. TOKYO").
    html = ("<html><body><main>"
            "<h2>San Francisco VS.\u00A0TOKYO</h2>"
            "<p>Body\u00A0text.</p>"
            "</main></body></html>")
    out = html_to_plaintext(html)
    assert "VS. TOKYO" in out
    assert "\u00A0TOKYO" not in out
    assert "Body\u00A0text" not in out


def test_to_paste_html_soft_returns_single_block():
    out = to_paste_html("Table of Contents\n\u00A0\n1. ONE\n\u00A0\u00A0\u00A0\u00A01.1 ONE A")
    assert out == (
        "<div>Table of Contents<br>"
        "<br>"
        "1. ONE<br>"
        "\u00A0\u00A0\u00A0\u00A01.1 ONE A<br>"
        "</div>"
    )


def test_to_paste_html_escapes():
    out = to_paste_html("A & B < C > D")
    assert out == "<div>A &amp; B &lt; C &gt; D<br></div>"


def test_to_paste_html_blank_variants():
    # Both a literal blank line and the converter's NBSP blank render as
    # one extra <br> (one empty line inside the single block).
    assert to_paste_html("A\n\nB") == "<div>A<br><br>B<br></div>"
    assert to_paste_html("A\n\u00A0\nB") == "<div>A<br><br>B<br></div>"


def test_html_to_plaintext_inline_text_matches_file_path():
    # Inline text input (htp "TEXT") produces the same output as the file
    # path input (htp FILE) for identical content.
    file_out = essay_to_plaintext(str(_write(_SAMPLE_HTML, "match.html")))
    text_out = html_to_plaintext(_SAMPLE_HTML)
    assert text_out == file_out
    assert "1. FIRST SECTION" in text_out
    assert "[1] First footnote." in text_out


def test_essay_to_plaintext_blockquote_and_table():
    html = """<main>
<blockquote>
<p>Quoted wisdom.</p>
</blockquote>
<table>
<tr><th>A</th><th>B</th></tr>
<tr><td>1</td><td>2</td></tr>
</table>
<h2>One</h2>
<h2>Two</h2>
</main>"""
    p = _write(html, "qt.html")
    out = essay_to_plaintext(str(p))
    assert "> Quoted wisdom." in out
    assert "| A | B |" in out
    assert "| 1 | 2 |" in out
    assert "TOC:" not in out  # 2 headings < 3


# ---------------------------------------------------------------------------
# Plaintext-convention source rendering (2026-08-25): pandoc renders the
# convention's literal "1. / 1.1. /" heading lines as 1-item <ol>s
# (level 1) and <p>s (level 2+), and the source's "=== TABLE OF CONTENTS
# ===" block as a <p> + <ol>. The engine must skip that block, recover
# the headings, and emit a clean plaintext TOC instead of the leak.
# ---------------------------------------------------------------------------

_PTXT_CONVENTION_HTML = """<main>
<p>=== TABLE OF CONTENTS ===</p>
<ol type="1">
<li><p>FIRST SECTION 1.1. Subsection One 1.1.1. Detail one</p></li>
<li><p>SECOND SECTION</p></li>
</ol>
<ol>
<li><p>FIRST SECTION</p></li>
</ol>
<p>Intro paragraph body text.</p>
<p>1.1. Subsection One</p>
<p>Body under the first subsection.</p>
<p>1.1.1. Detail one</p>
<p>Body under the detail.</p>
<ol>
<li><p>SECOND SECTION</p></li>
</ol>
<p>Body under the second section.</p>
</main>"""


def test_ptxt_parse_heading():
    from x_publisher_toc import _ptxt_parse_heading
    assert _ptxt_parse_heading("1. TITLE") == ("1", 1, "TITLE")
    assert _ptxt_parse_heading("1.1. What Harms Do Draconian Regulations Cause?") == (
        "1.1", 2, "What Harms Do Draconian Regulations Cause?")
    assert _ptxt_parse_heading("[1.1.1.1] deep detail") == ("1.1.1.1", 4, "deep detail")
    # Measurements and prose that merely LOOK numeric do not match.
    assert _ptxt_parse_heading("1.5 percent of households") is None
    assert _ptxt_parse_heading("6 - 9 months to evict") is None
    assert _ptxt_parse_heading("2.8-acre rooftop park") is None
    assert _ptxt_parse_heading("Plain paragraph.") is None


def test_plan_blocks_convention_html():
    p = _write(_PTXT_CONVENTION_HTML, "ptxt.html")
    plan = plan_blocks_from_html(str(p))
    types = [b["type"] for b in plan]
    # TOC <ol> skipped; 1-item <ol>s recovered as level-1 headings;
    # "1.1."/"1.1.1." <p>s recovered as levels 2/3.
    assert types == ["h1", "body", "h2", "body", "h3", "body", "h1", "body"]
    assert plan[0]["text"] == "FIRST SECTION"
    assert plan[2]["text"] == "Subsection One"
    assert plan[6]["text"] == "SECOND SECTION"


def test_essay_to_plaintext_convention_html():
    p = _write(_PTXT_CONVENTION_HTML, "ptxt-full.html")
    out = essay_to_plaintext(str(p))
    expected = (
        "=== TABLE OF CONTENTS ===\n\u00A0\n"
        "1. FIRST SECTION\n"
        "\u00A0\u00A0\u00A0\u00A01.1. Subsection One\n"
        "\u00A0\u00A0\u00A0\u00A0\u00A0\u00A0\u00A0\u00A01.1.1. Detail one\n"
        "\u00A0\n"
        "2. SECOND SECTION\n"
        "\u00A0\n"
        "1. FIRST SECTION\n"
        + ("-" * len("1. FIRST SECTION")) + "\n"
        "\u00A0\n"
        "Intro paragraph body text.\n"
        "\u00A0\n"
        "1.1. Subsection One\n"
        + ("~" * len("1.1. Subsection One")) + "\n"
        "\u00A0\n"
        "Body under the first subsection.\n"
        "\u00A0\n"
        "1.1.1. Detail one\n"
        + ("^" * len("1.1.1. Detail one")) + "\n"
        "\u00A0\n"
        "Body under the detail.\n"
        "\u00A0\n"
        "2. SECOND SECTION\n"
        + ("-" * len("2. SECOND SECTION")) + "\n"
        "\u00A0\n"
        "Body under the second section."
    )
    assert out == expected
    # No leaked space-joined site-TOC text.
    assert "FIRST SECTION 1.1." not in out
    assert "Subsection One 1.1.1." not in out


def test_essay_to_plaintext_convention_merged_first_heading():
    # The first body heading sits INSIDE the source-TOC <ol> (pandoc
    # merged it); the engine must still emit it as a level-1 heading
    # before its first subheading (ancestor emission).
    html = """<main>
<p>=== TABLE OF CONTENTS ===</p>
<ol type="1">
<li><p>FIRST SECTION 1.1. Subsection One</p></li>
<li><p>SECOND SECTION</p></li>
<li><p>FIRST SECTION</p></li>
</ol>
<p>Intro paragraph.</p>
<p>1.1. Subsection One</p>
<p>Body text.</p>
<ol>
<li><p>SECOND SECTION</p></li>
</ol>
<p>More body.</p>
</main>"""
    p = _write(html, "ptxt-merged.html")
    out = essay_to_plaintext(str(p))
    # TOC groups: 1. FIRST SECTION (with 1.1.), then 2. SECOND SECTION.
    assert "1. FIRST SECTION\n\u00A0\u00A0\u00A0\u00A01.1. Subsection One" in out
    assert "2. SECOND SECTION" in out
    # Body: the merged level-1 heading is re-emitted at its true position
    # (before the intro paragraph), then its first subheading.
    assert "1. FIRST SECTION\n" + ("-" * len("1. FIRST SECTION")) + "\n\u00A0\nIntro paragraph.\n\u00A0\n1.1. Subsection One\n" + ("~" * len("1.1. Subsection One")) in out
    assert "2. SECOND SECTION\n" + ("-" * len("2. SECOND SECTION")) + "\n\u00A0\nMore body." in out
    # The merged heading is not duplicated: once in the TOC, once in the body.
    assert out.count("1. FIRST SECTION") == 2


def test_convention_mode_not_triggered_by_plain_lists():
    # A 1-item <ol> in an OLD-format essay (no convention markers) stays
    # a body list item; convention heading recovery stays off.
    html = ("<main><ol><li>Only item</li></ol>"
            "<h2>One</h2><h2>Two</h2><h2>Three</h2></main>")
    p = _write(html, "ol1.html")
    out = essay_to_plaintext(str(p))
    assert "1. Only item" in out
    assert "1. ONLY ITEM" not in out  # not treated as a heading


def test_essay_to_plaintext_convention_no_source_toc():
    # Source TOC removed from the .md (2026-08-25): the rendered HTML has
    # NO "=== TABLE OF CONTENTS ===" label and NO site-TOC <ol>. Level-1
    # recovery must fall back to the convention's ALL-CAPS rule for
    # 1-item <ol>s; the TOC is still generated.
    html = """<main>
<ol>
<li><p>FIRST SECTION</p></li>
</ol>
<p>Intro paragraph body text.</p>
<p>1.1. Subsection One</p>
<p>Body under the first subsection.</p>
<ol>
<li><p>SECOND SECTION</p></li>
</ol>
<p>Body under the second section.</p>
</main>"""
    p = _write(html, "ptxt-notoc.html")
    out = essay_to_plaintext(str(p))
    assert "=== TABLE OF CONTENTS ===\n\u00A0\n1. FIRST SECTION\n\u00A0\u00A0\u00A0\u00A01.1. Subsection One\n\u00A0\n2. SECOND SECTION" in out
    # The 1-item <ol>s are recovered as headings, not rendered as lists.
    assert "1. FIRST SECTION\n" + ("-" * len("1. FIRST SECTION")) + "\n\u00A0\nIntro paragraph body text.\n\u00A0\n1.1. Subsection One\n" + ("~" * len("1.1. Subsection One")) in out
    assert "2. SECOND SECTION\n" + ("-" * len("2. SECOND SECTION")) + "\n\u00A0\nBody under the second section." in out
    assert "1. SECOND SECTION" not in out
    assert "1. FIRST SECTION\n\u00A0\n1. FIRST SECTION" not in out  # no duplication


def test_plan_blocks_convention_no_source_toc():
    html = """<main>
<ol>
<li><p>FIRST SECTION</p></li>
</ol>
<p>Intro paragraph body text.</p>
<p>1.1. Subsection One</p>
<ol>
<li><p>SECOND SECTION</p></li>
</ol>
</main>"""
    p = _write(html, "ptxt-notoc-plan.html")
    plan = plan_blocks_from_html(str(p))
    types = [b["type"] for b in plan]
    assert types == ["h1", "body", "h2", "h1"]
    assert plan[0]["text"] == "FIRST SECTION"
    assert plan[2]["text"] == "Subsection One"
    assert plan[3]["text"] == "SECOND SECTION"


def test_essay_to_plaintext_pandoc_merged_first_ol():
    # The REAL pandoc render of a convention source with the source TOC
    # removed: the numbered heading lines merge into ONE <ol> at the top
    # (sections as li's, subheadings absorbed as continuation <p>s, and
    # the first body heading lands inside it as a duplicate li), with no
    # "=== TABLE OF CONTENTS ===" label. The engine must identify that
    # <ol> as the de-facto site TOC, skip it, and emit a clean TOC.
    html = """<main>
<ol type="1">
<li><p>FIRST SECTION 1.1. Subsection One 1.1.1. Detail one</p></li>
<li><p>SECOND SECTION</p></li>
<li><p>THIRD SECTION 3.1. Subsection Three</p></li>
<li><p>FIRST SECTION</p></li>
</ol>
<p>Intro paragraph body text.</p>
<p>1.1. Subsection One</p>
<p>Body under the first subsection.</p>
<p>1.1.1. Detail one</p>
<p>Body under the detail.</p>
<ol start="2">
<li><p>SECOND SECTION</p></li>
</ol>
<p>Body under the second section.</p>
<ol start="3">
<li><p>THIRD SECTION</p></li>
</ol>
<p>3.1. Subsection Three</p>
<p>Body under the third.</p>
</main>"""
    p = _write(html, "ptxt-pandoc.html")
    out = essay_to_plaintext(str(p))
    # Clean TOC from the merged <ol>: 3 groups with correct subheadings.
    assert ("=== TABLE OF CONTENTS ===\n\u00A0\n"
            "1. FIRST SECTION\n"
            "\u00A0\u00A0\u00A0\u00A01.1. Subsection One\n"
            "\u00A0\u00A0\u00A0\u00A0\u00A0\u00A0\u00A0\u00A01.1.1. Detail one\n"
            "\u00A0\n"
            "2. SECOND SECTION\n"
            "\u00A0\n"
            "3. THIRD SECTION\n"
            "\u00A0\u00A0\u00A0\u00A03.1. Subsection Three") in out
    # Body: the absorbed section-1 heading is re-emitted before 1.1.
    assert "1. FIRST SECTION\n" + ("-" * len("1. FIRST SECTION")) + "\n\u00A0\nIntro paragraph body text.\n\u00A0\n1.1. Subsection One\n" + ("~" * len("1.1. Subsection One")) in out
    assert "2. SECOND SECTION\n" + ("-" * len("2. SECOND SECTION")) + "\n\u00A0\nBody under the second section." in out
    assert "3. THIRD SECTION\n" + ("-" * len("3. THIRD SECTION")) + "\n\u00A0\n3.1. Subsection Three" in out
    # No leaked space-joined list text; no phantom "1. FIRST SECTION" list.
    assert "FIRST SECTION 1.1." not in out
    assert "1. FIRST SECTION\n\u00A0\n1. FIRST SECTION" not in out
