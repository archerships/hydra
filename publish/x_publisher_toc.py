"""Pure logic: essay heading -> plaintext simulated heading line.

Decision 2026-08-17 (supersedes the composer-block-type approach):
instead of fighting X's Heading/Subheading composer blocks (which fuse
and produce a bad auto-TOC), headings are rendered as PLAINTEXT body
blocks in a numbered hierarchy:

    === TABLE OF CONTENTS ===

    1. HEADING LEVEL 1

    1.1. Heading Level 2

    1.1.1. Heading level 3

    [1.1.1.1] heading level 4

    [1.1.1.1.1] heading level 5

Rules per level (Table.11.CaseConv, user 2026-08-17):
- level 1 (h2): ALL CAPS title (H2); the H1 (inline "=== TABLE OF
  CONTENTS ===") belongs to the TOC label only
- level 2 (h3): Title Case title (H3)
- level 3 (h4): sentence case title (H4)
- level 4 (h5): lowercase bracketed run-in (H5)
- level 5 (h6): lowercase bracketed run-in

Numbering is multi-level with counters that restart per parent
(1, 1.1, 1.1.1, 1.1.2, 2, 2.1, ...).

Platform spacing note (X Articles composer): the output uses ONE blank
line between EVERY content element (TOC, intro, headings, paragraphs,
lists, footnotes), while INSIDE an element everything stays tight (TOC
subheading groups, list items). Blank lines are NBSP-only lines
("\u00A0"): a literal empty line in the paste renders as TWO empty
composer blocks, and an NBSP line as one normal-height block (verified
2026-08-17). The user's manual edit of the sample draft defines the
exact layout (2026-08-17): caps TOC label, one blank between every
element, "FOOTNOTES" label above the "---".

Extends the Character-Based Structural Convention (Response.12.PtxtHdg):
- TOC: a plaintext decimal TOC (inline "=== TABLE OF CONTENTS ===" H1
  label + grouped entries mirroring the body heading labels) is emitted
  when the essay has >= TOC_MIN_HEADINGS content headings, matching
  render-post build_toc's rule. Top-level groups separated by one blank
  line; subheadings single-spaced, indented 4 spaces per level.
- Footnotes: the end-of-article list is rendered as "---" separator then
  "[N] Citation" lines (inline [N] markers are handled in
  SEGMENT_PREP_JS, which converts pandoc <sup> refs to visible [N]).

This module is the TESTABLE ORACLE for x-publisher.py's compose loop.
is_chrome_heading / plan_blocks_from_html keep the chrome-heading skip
list and the block-walk rules in sync with SEGMENT_PREP_JS.
"""

from pathlib import Path
import html
import re

from bs4 import BeautifulSoup, Tag

CHROME_HEADINGS = {"notes", "want to stay in touch", "support my work"}

CHROME_IDS = {
    "contents",
    "post-toc",
    "site-breadcrumb",
    "site-nav",
    "site-footer",
    "also-published",
    "contact-snippet",
}

SKIP_TAGS = {"figure", "img"}

HEADING_LEVEL = {"h2": 1, "h3": 2, "h4": 3, "h5": 4, "h6": 5}
# Body-heading underline char by essay heading depth (2026-08-27).
_UNDERLINE_BY_TAG = {"h2": "-", "h3": "~", "h4": "^", "h5": "~", "h6": "^"}
RULE_WIDTH = 50
RULE_L1 = "=" * RULE_WIDTH
RULE_L2 = "-" * RULE_WIDTH

# Mirror render-post build_toc: the website TOC appears only when the
# post has at least 3 qualifying headings.
TOC_MIN_HEADINGS = 3

# Plaintext-convention source detection (2026-08-25): pandoc renders the
# convention's literal heading lines from the source .md -- level-1 lines
# ("1. TITLE") become ONE-ITEM <ol> lists (the marker is consumed as the
# list number), level-2+ lines ("1.1. Title") become <p> paragraphs, and
# the source's own "=== TABLE OF CONTENTS ===" block becomes a <p> label
# followed by an <ol> of the whole site TOC. Without special handling the
# site TOC <ol> leaks into the article and the headings are invisible to
# toc_plaintext. Convention mode kicks in when the label <p> or a
# heading-pattern <p> is present; old-format essays (real h2-h6) are
# unaffected.
PTXT_HEADING_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,4})\.\s+(.+)$")
PTXT_BRACKET_RE = re.compile(r"^\[(\d{1,2}(?:\.\d{1,2}){1,4})\]\.?\s*(.*)$")
PTXT_TOC_LABEL_RE = re.compile(r"^===\s*TABLE OF CONTENTS\s*===$", re.IGNORECASE)
_PTXT_SUBHEAD_SPLIT_RE = re.compile(r"(?<=\s)(?=\d{1,2}(?:\.\d{1,2}){1,4}\.\s)")
_PTXT_HTML_TAG = {1: "h2", 2: "h3", 3: "h4", 4: "h5", 5: "h6"}


def _ptxt_level(num: str) -> int:
    """Heading level from a decimal number string (capped at 5)."""
    return min(num.count(".") + 1, 5)


def _ptxt_parse_heading(text: str):
    """Return (num, level, title) if text is a plaintext-convention heading
    line ("1. Title", "1.1. Title", "[1.1.1.1] Title"); else None.

    Strict on the trailing dot: "1.5 percent" (a measurement) does NOT
    match because after the last digit the next char must be a '.' then a
    space."""
    m = PTXT_HEADING_RE.match(text)
    if m:
        return m.group(1), _ptxt_level(m.group(1)), m.group(2)
    m = PTXT_BRACKET_RE.match(text)
    if m:
        return m.group(1), _ptxt_level(m.group(1)), m.group(2)
    return None


def _ptxt_toc_tree(ol) -> list[dict]:
    """Parse the source-TOC <ol> (pandoc's render of the convention's
    literal TOC block) into heading dicts {num, level, title} in document
    order. Each <li> holds one level-1 title, optionally followed by its
    space-joined subheadings with their decimal prefixes intact
    ("1.1. What Harms... 1.1.1. Homelessness..."). Duplicate sections
    (e.g. a body heading pandoc merged into the TOC list) are dropped.
    Level-1 numbers come from the list position (the marker is consumed
    by pandoc)."""
    tree = []
    seen = set()
    for i, li in enumerate(ol.find_all("li", recursive=False), 1):
        text = li.get_text(" ", strip=True).replace("\u00A0", " ")
        if not text:
            continue
        segs = [s.strip() for s in _PTXT_SUBHEAD_SPLIT_RE.split(text) if s.strip()]
        l1_title = segs[0]
        if ("1", l1_title.upper()) in seen:
            continue
        seen.add(("1", l1_title.upper()))
        tree.append({"num": str(i), "level": 1, "title": l1_title})
        for seg in segs[1:]:
            parsed = _ptxt_parse_heading(seg)
            if parsed:
                num, level, title = parsed
                tree.append({"num": num, "level": level, "title": title})
    return tree


class HeadingNumberer:
    """Tracks per-level counters (1..5) for the plaintext heading scheme.

    next_number(level) increments that level's counter, resets all
    deeper counters to 0, and returns the dotted number (e.g. "1.2.3").
    """

    def __init__(self) -> None:
        self.counters = [0, 0, 0, 0, 0]

    def next_number(self, level: int) -> str:
        self.counters[level - 1] += 1
        for i in range(level, 5):
            self.counters[i] = 0
        return ".".join(str(c) for c in self.counters[:level])


def _heading_label(num: str, level: int) -> str:
    """Decimal label per Table.11.CaseConv Pattern A (2026-08-17):
    levels 1-3 use "1.", "1.1.", "1.1.1."; levels 4+ use the H5
    bracketed run-in "[1.1.1.1].""" 
    if level >= 4:
        return f"[{num}]"
    return f"{num}."

def _preserve_proper(cased: str, src: str) -> str:
    """Re-capitalize words that were capitalized in the source (proper names)
    after a sentence-case/lowercase transform.

    The case transforms only change case, never the word sequence, so the
    output and the source share the same word order. For each aligned pair:
    all-caps source words (acronyms like FAR, WOHA) are restored to all caps;
    words starting with an uppercase letter in the source (Brock, Virginia)
    get their first letter capitalized again; lowercase source words keep the
    transform's output. This keeps proper names correct in otherwise
    lowercase headers (user guideline 2026-08-17)."""
    out_words = cased.split()
    src_words = src.split()
    if len(out_words) != len(src_words):
        return cased
    res = []
    for o, s in zip(out_words, src_words):
        i, j = 0, len(s)
        while i < j and not s[i].isalnum():
            i += 1
        while j > i and not s[j - 1].isalnum():
            j -= 1
        if i >= j:
            res.append(o)  # punctuation-only word
            continue
        core = s[i:j]
        if len(core) > 1 and core.isupper():
            res.append(o.upper())
        elif core[:1].isupper():
            res.append(o[:i] + o[i].upper() + o[i + 1:])
        else:
            res.append(o)
    return " ".join(res)


def _heading_title(level: int, text: str) -> str:
    """Case per Table.11.CaseConv (user guideline 2026-08-17):
    L1 ALL CAPS (H2); L2 Title Case (H3, capitalize each word, preserving
    existing capitals/acronyms); L3 sentence case / Initial Cap (H4);
    L4+ lowercase (H5). L3/L4+ restore capitalization of source-capitalized
    words (proper names, acronyms) via _preserve_proper."""
    t = text.strip()
    if level == 1:
        return t.upper()
    if level == 2:
        return re.sub(r"\S+", lambda m: m.group(0)[0].upper() + m.group(0)[1:], t)
    if level == 3:
        return _preserve_proper(t[:1].upper() + t[1:].lower(), t)
    return _preserve_proper(t.lower(), t)


def heading_plaintext(tag: str, text: str, numberer: HeadingNumberer = None,
                      num: str = None) -> str:
    """Render one essay heading as its plaintext block (numbered title +
    underline rule). `tag` must be in HEADING_LEVEL. No trailing blank line:
    the composer splits pasted text at newlines, so a trailing newline would
    add an empty block (see module docstring).

    Body-heading underlining convention (2026-08-27):
      - the underline sits BELOW the heading text (a single underline char
        line under the heading)
      - underline char by essay heading depth: h2 (H2) -> "-", h3 (H3) -> "~",
        h4 (H4) -> "^"
      - the underline REPEATS the rendered heading line's exact character
        count (label + title), so it visually underlines the text
      - the caller (compose loop) supplies one blank line above the heading
        and one below the underline before body text
    Decimal labels: "1.", "1.1.", "1.1.1." then "[1.1.1.1]" (unchanged).

    Pass a precomputed `num` to keep numbering in sync with a TOC built
    from the same sequence; otherwise `numberer` is consumed (one number
    per call, in call order).
    """
    level = HEADING_LEVEL[tag]
    if num is None:
        if numberer is None:
            raise ValueError("heading_plaintext needs either a numberer or a num")
        num = numberer.next_number(level)
    title = _heading_title(level, text)
    label = _heading_label(num, level)
    line = f"{label} {title}"
    # underline char by depth: h2->'-', h3->'~', h4->'^'
    under = (_UNDERLINE_BY_TAG.get(tag) or '-')
    return line + "\n" + (under * len(line))


def assign_numbers(blocks: list[dict]) -> list[dict]:
    """Set 'num' on every heading block (dicts with 'tag' in
    HEADING_LEVEL), consuming one shared HeadingNumberer in order.
    Mutates and returns the list. Mirrors compose_body_segments'
    numbering pre-pass, so a TOC and the body headings share numbers.
    """
    numberer = HeadingNumberer()
    for b in blocks:
        if b.get("tag") in HEADING_LEVEL:
            b["num"] = numberer.next_number(HEADING_LEVEL[b["tag"]])
    return blocks


def toc_plaintext(blocks: list[dict]) -> str:
    """Plaintext decimal TOC per the structural convention
    (Table.11.CaseConv, Pattern A):

        ==================================================
        TABLE OF CONTENTS
        ==================================================

        1. FIRST SECTION
            1.1. Subsection One
                1.1.1. Minor topic
                    [1.1.1.1] sub-point

        2. SECOND SECTION

    Entry labels mirror the body heading labels exactly (same number,
    same case), so the TOC numbers always match the body numbers. The
    H1 label carries the surrounding ==== rules; levels are H2 ALL CAPS,
    H3 Title Case, H4 sentence case, H5 bracketed run-in. Top-level
    entries (level 1) start a group; subheadings are single-spaced
    underneath and indented four spaces per level. Groups are separated
    by ONE blank line. Returns "" when fewer than TOC_MIN_HEADINGS
    content headings (mirrors the website TOC rule). Blocks need 'tag',
    'text', 'num'.
    """
    heads = [b for b in blocks if b.get("tag") in HEADING_LEVEL]
    if len(heads) < TOC_MIN_HEADINGS:
        return ""
    # Groups: one per top-level heading (level 1), with its subheadings
    # single-spaced underneath, indented per level. Top-level groups are
    # separated by ONE blank line. Indentation AND blank lines use
    # NON-BREAKING SPACES: X Articles strips leading ASCII whitespace and
    # renders a literal "\n\n" as TWO empty blocks (verified 2026-08-17);
    # a line containing only \u00A0 survives as one normal-height block
    # (invisible content), i.e. exactly one blank line.
    INDENT = "\u00A0"
    groups: list[list[str]] = []
    for b in heads:
        level = HEADING_LEVEL[b["tag"]]
        label = _heading_label(b["num"], level)
        title = _heading_title(level, b["text"])
        entry = f"{label} {title}"
        if level == 1 or not groups:
            # A level-1 entry starts a new group; if the first heading is
            # not level 1 (malformed/edge document), start a group anyway
            # instead of crashing on groups[-1].
            groups.append([entry])
        else:
            indent = INDENT * (4 * (level - 1))
            groups[-1].append(indent + entry)
    blank = "\n" + INDENT + "\n"
    label_block = "=== TABLE OF CONTENTS ==="
    return label_block + blank + blank.join("\n".join(g) for g in groups)


def footnotes_plaintext(items: list[str]) -> str:
    """End-of-article footnote list. Header uses the overline/underline rule
    convention (2026-08-27):

        ==========
        FOOTNOTES
        ==========

        [1] First citation.
        [2] Second citation.

        ---

    The overline and underline repeat the FOOTNOTES text's exact character
    count. items are the footnote <li> texts in order; numbering is
    positional 1..N so it matches the inline [N] markers. Returns "" when
    there are no non-empty items (no label/separator either). No trailing
    blank line (see module docstring).
    """
    header = "FOOTNOTES"
    rule = "=" * len(header)
    lines = [rule, header, rule]
    any_item = False
    for i, item in enumerate(items, 1):
        t = item.strip()
        if not t:
            continue
        if not any_item and len(lines) == 3:
            # blank line between the header rule and the first citation
            lines.append("\u00A0")
        any_item = True
        lines.append(f"[{i}] {t}")
    if not any_item:
        return ""
    lines.append("\u00A0")
    lines.append("---")
    return "\n".join(lines)


def _ptxt_first_ol_tree(children) -> list[dict]:
    """If the FIRST element of main is an <ol> whose contents parse as a
    convention heading tree (>= TOC_MIN_HEADINGS entries, first entry an
    ALL-CAPS level-1 title), return the tree.

    Pandoc renders a convention source's numbered heading lines as ONE
    merged <ol> at the top of the body: each section becomes an <li> with
    its subheading lines absorbed as continuation <p>s (space-joined), and
    the first body heading lands inside the same list. That <ol> is the
    de-facto site TOC and must be skipped (the engine emits its own).
    Returns [] for any other first element (real lists, intro text,
    label <p>s, old-format headings)."""
    for el in children:
        if el.name.lower() != "ol":
            return []
        cand = _ptxt_toc_tree(el)
        if (len(cand) >= TOC_MIN_HEADINGS and cand
                and cand[0]["level"] == 1
                and cand[0]["title"].upper() == cand[0]["title"]):
            return cand
        return []
    return []


def is_chrome_heading(text: str) -> bool:
    """True for renderer-added chrome headings excluded from the TOC
    (Notes, Want to stay in touch?, Support my work)."""
    return text.strip().lower().rstrip("?.") in CHROME_HEADINGS


def _strip_chrome_ids(soup) -> None:
    """Remove elements whose id OR class is in CHROME_IDS, in place
    (mirrors the JS `el.remove()` loop in SEGMENT_PREP_JS -- the
    rendered post-TOC nav is class="post-toc" with NO id)."""
    for el in list(soup.find_all(True)):
        if not isinstance(el, Tag) or el.attrs is None:
            continue
        if el.get("id") in CHROME_IDS:
            el.decompose()
            continue
        cls = el.get("class") or []
        if any(c in CHROME_IDS for c in cls):
            el.decompose()


def plan_blocks_from_html(html_path: str) -> list[dict]:
    """Walk <main> children (element-only) and emit {type, tag, text} per
    block. Skips chromeIds elements, figures/imgs, img-only <p> wrappers,
    recs-wrap divs, empty blocks, the footnotes section (its items are
    collected separately), and chrome h2/h3s. Mirror of SEGMENT_PREP_JS
    skip rules. `type` is the plaintext heading level
    ("h1"/"h2"/... ) or "body".
    """
    soup = BeautifulSoup(Path(html_path).read_text(encoding="utf-8"), "html.parser")
    _strip_chrome_ids(soup)
    main = soup.find("main") or soup.find("body")
    if main is None:
        return []

    children = [c for c in main.children if hasattr(c, "name") and c.name]
    # Plaintext-convention mode: the source TOC label <p>, any
    # heading-pattern <p>, or a 1-item ALL-CAPS <ol> (convention L1
    # heading line) marks a convention-rendered document.
    convention = False
    for el in children:
        tag = el.name.lower()
        if tag == "p":
            t = el.get_text(" ", strip=True).replace("\u00A0", " ")
            if PTXT_TOC_LABEL_RE.match(t) or _ptxt_parse_heading(t):
                convention = True
                break
        elif tag == "ol" and len(el.find_all("li", recursive=False)) == 1:
            t = el.find("li").get_text(" ", strip=True).replace("\u00A0", " ")
            if t and t.upper() == t:
                convention = True
                break

    out = []
    toc_tree = _ptxt_first_ol_tree(children) if convention else []
    tree_cursor = 0
    toc_ol_pending = False
    toc_tree_consumed = False
    for el in children:
        tag = el.name.lower()
        if tag in SKIP_TAGS:
            continue
        if tag == "section" and el.get("id") == "footnotes":
            continue
        if tag == "p" and el.find("img") and not el.get_text(" ", strip=True):
            continue
        cls = el.get("class") or []
        if "recs-wrap" in cls:
            continue
        text = el.get_text(" ", strip=True).replace("\u00A0", " ")
        if not text:
            continue
        if tag in ("h2", "h3") and is_chrome_heading(text):
            continue
        # Plaintext-convention: skip the source-TOC block, then recover
        # the heading structure pandoc flattened into <p>s and 1-item
        # <ol>s (see the PTXT_* helpers).
        if tag == "p" and PTXT_TOC_LABEL_RE.match(text):
            toc_ol_pending = True
            continue
        if toc_ol_pending:
            if tag == "ol":
                toc_ol_pending = False
                toc_tree = _ptxt_toc_tree(el)
                toc_tree_consumed = True
                continue
            toc_ol_pending = False
        if tag == "ol" and toc_tree and not toc_tree_consumed:
            # The first <ol> IS the pandoc-merged convention heading list
            # (the de-facto site TOC); its tree was parsed up front.
            toc_tree_consumed = True
            continue
        heading = None
        if convention and tag == "p":
            heading = _ptxt_parse_heading(text)
        elif convention and tag == "ol" and len(el.find_all("li", recursive=False)) == 1:
            li_text = el.find("li").get_text(" ", strip=True).replace("\u00A0", " ")
            if li_text and (
                any(t["level"] == 1 and t["title"].upper() == li_text.upper()
                    for t in toc_tree)
                or li_text.upper() == li_text  # convention L1 lines are ALL CAPS
            ):
                heading = ("", 1, li_text)
        if heading is None and toc_tree and tree_cursor == 0 and toc_tree[0]["level"] == 1:
            # The pandoc-merged first <ol> absorbed the section-1 heading
            # (it is NOT in the body); emit it before the first body
            # content so the section title leads its section.
            t = toc_tree[0]
            out.append({"type": "h1", "tag": "h2", "text": t["title"][:80]})
            tree_cursor = 1
        if heading:
            _, level, title = heading
            # Emit un-emitted tree ancestors first (covers a level-1
            # heading pandoc merged into the source-TOC <ol>).
            while tree_cursor < len(toc_tree) and toc_tree[tree_cursor]["level"] < level:
                t = toc_tree[tree_cursor]
                out.append({"type": f"h{t['level']}", "tag": _PTXT_HTML_TAG[t["level"]],
                            "text": t["title"][:80]})
                tree_cursor += 1
            if (tree_cursor < len(toc_tree)
                    and toc_tree[tree_cursor]["level"] == level
                    and toc_tree[tree_cursor]["title"].upper() == title.upper()):
                t = toc_tree[tree_cursor]
                out.append({"type": f"h{level}", "tag": _PTXT_HTML_TAG[level],
                            "text": t["title"][:80]})
                tree_cursor += 1
            else:
                out.append({"type": f"h{level}", "tag": _PTXT_HTML_TAG[level],
                            "text": title[:80]})
            continue
        block_type = "body" if tag not in HEADING_LEVEL else f"h{HEADING_LEVEL[tag]}"
        out.append({"type": block_type, "tag": tag, "text": text[:80]})
    return out


def _footnotes_from_soup(soup) -> list[str]:
    """Extract the footnote <li> texts from section#footnotes in order.
    Mirror of the SEGMENT_PREP_JS footnotes handler."""
    section = soup.find("section", id="footnotes")
    if not isinstance(section, Tag):
        return []
    items = []
    for li in section.select("ol > li"):
        for back in li.find_all("a", class_="footnote-back"):
            back.decompose()
        items.append(li.get_text(" ", strip=True).replace("\u00A0", " "))
    return items


def footnotes_from_html(html_path: str) -> list[str]:
    """Extract the footnote <li> texts from an essay HTML file, in order."""
    soup = BeautifulSoup(Path(html_path).read_text(encoding="utf-8"), "html.parser")
    return _footnotes_from_soup(soup)


def _inline_footnote_refs(soup) -> None:
    """Replace pandoc footnote-ref anchors (<a class="footnote-ref"
    href="#fnN"><sup>N</sup></a>) with visible inline [N] text, in place.
    Mirror of the SEGMENT_PREP_JS sup conversion."""
    for a in soup.select("a.footnote-ref[href^='#fn']"):
        n = a.get("href", "")[3:]
        a.replace_with(f"[{n}]")


def _render_list(el, ordered: bool) -> list[str]:
    """Render a ul/ol element as "- item" / "1. item" lines."""
    lines = []
    for i, li in enumerate(el.find_all("li", recursive=False), 1):
        t = li.get_text(" ", strip=True).replace("\u00A0", " ")
        if t:
            lines.append(f"{i}. {t}" if ordered else f"- {t}")
    return lines


def _join_list_items(items: list[str]) -> str:
    """Join rendered list items per the user's size rule (2026-08-17):
    short one-line items stay tight ("\n"); items that are paragraph-long
    (wrap in the composer column) are separated by one blank line
    ("\n\u00A0\n"). One line ~= 80 chars in the X Articles column."""
    sep = "\n\u00A0\n" if any(len(i) > 80 for i in items) else "\n"
    return sep.join(items)


def _render_table(el) -> list[str]:
    """Render a real table as pipe rows ("| a | b |")."""
    lines = []
    for tr in el.find_all("tr"):
        cells = []
        for c in tr.find_all(["th", "td"]):
            t = c.get_text(" ", strip=True).replace("\u00A0", " ")
            if t:
                cells.append(t)
        if cells:
            lines.append("| " + " | ".join(cells) + " |")
    return lines


def _img_placeholder(el):
    """Return the placeholder line for an image element, or None.

    Format: "[IMAGE: <basename>]" -- a distinctive, findable marker that
    the second-pass installer replaces with a real image insert (user
    proposal 2026-08-17: paste plaintext with placeholders, then a
    second pass swaps each placeholder for the image at that position).
    """
    img = el if el.name == "img" else el.find("img")
    if img is None:
        return None
    src = img.get("src", "")
    if "img/" not in src:
        return None
    name = src.rsplit("/", 1)[-1]
    return f"[IMAGE: {name}]"


def _plaintext_from_soup(soup, image_placeholders: bool = False) -> str:
    """Convert a parsed essay document to the full-document plaintext
    convention (Character-Based Structural Convention, platform-adapted):

        === TABLE OF CONTENTS ===

        1. FIRST SECTION
            1.1. Subsection One

        2. SECOND SECTION

        1. FIRST SECTION

        Body paragraph text with inline [1], [2] footnote markers.

        1.1. Subsection One

        - bullet item
        1. numbered item

        > quoted paragraph

        | a | b |

        FOOTNOTES
        ---

        [1] Citation.

    One multi-line chunk, designed for a single clipboard paste into the
    X Articles composer. Blank lines appear between EVERY content element
    (one NBSP-only line each -- a literal empty line pastes as TWO empty
    composer blocks); heading blocks, TOC subheading groups, and list
    items stay tight. The footnotes section is "FOOTNOTES" label +
    "---" + one blank + "[N] Citation" lines.

    Chrome (site nav, post-TOC nav, contact snippet, renderer-added
    headings) is dropped; the footnotes section becomes the "FOOTNOTES"
    label + "---" + "[N] Citation" list; real tables become pipe rows;
    figures/imgs are skipped by default (the hero is the cover, inline
    images are stripped for X). With image_placeholders=True each skipped
    image emits a "[IMAGE: <basename>]" part at its document position
    (the single-block HTML paste + second-pass installer flow).
    """
    _strip_chrome_ids(soup)
    _inline_footnote_refs(soup)
    main = soup.find("main") or soup.find("body")
    if main is None:
        return ""

    blocks = []
    children = [c for c in main.children if isinstance(c, Tag)]
    # Plaintext-convention mode: the source TOC label <p>, any
    # heading-pattern <p>, or a 1-item ALL-CAPS <ol> (convention L1
    # heading line) marks a convention-rendered document.
    convention = False
    for c in children:
        tag = c.name.lower()
        if tag == "p":
            t = c.get_text(" ", strip=True).replace("\u00A0", " ")
            if PTXT_TOC_LABEL_RE.match(t) or _ptxt_parse_heading(t):
                convention = True
                break
        elif tag == "ol" and len(c.find_all("li", recursive=False)) == 1:
            t = c.find("li").get_text(" ", strip=True).replace("\u00A0", " ")
            if t and t.upper() == t:
                convention = True
                break
    toc_tree = _ptxt_first_ol_tree(children) if convention else []
    tree_cursor = 0
    toc_ol_pending = False
    toc_tree_consumed = False
    for c in children:
        tag = c.name.lower()
        if tag in SKIP_TAGS:
            if image_placeholders:
                ph = _img_placeholder(c)
                if ph:
                    blocks.append({"tag": "imgph", "el": c, "text": ph})
            continue
        if tag == "section" and c.get("id") == "footnotes":
            continue
        cls = c.get("class") or []
        if "recs-wrap" in cls:
            continue
        if tag == "p" and c.find("img") and not c.get_text(" ", strip=True):
            if image_placeholders:
                ph = _img_placeholder(c)
                if ph:
                    blocks.append({"tag": "imgph", "el": c, "text": ph})
            continue
        text = c.get_text(" ", strip=True).replace("\u00A0", " ")
        if not text:
            continue
        if tag in ("h2", "h3") and is_chrome_heading(text):
            continue
        # Plaintext-convention: skip the source-TOC block, then recover
        # the heading structure pandoc flattened into <p>s and 1-item
        # <ol>s (see the PTXT_* helpers).
        if tag == "p" and PTXT_TOC_LABEL_RE.match(text):
            toc_ol_pending = True
            continue
        if toc_ol_pending:
            if tag == "ol":
                toc_ol_pending = False
                toc_tree = _ptxt_toc_tree(c)
                toc_tree_consumed = True
                continue
            toc_ol_pending = False
        if tag == "ol" and toc_tree and not toc_tree_consumed:
            # The first <ol> IS the pandoc-merged convention heading list
            # (the de-facto site TOC); its tree was parsed up front.
            toc_tree_consumed = True
            continue
        heading = None
        if convention and tag == "p":
            heading = _ptxt_parse_heading(text)
        elif convention and tag == "ol" and len(c.find_all("li", recursive=False)) == 1:
            li_text = c.find("li").get_text(" ", strip=True).replace("\u00A0", " ")
            if li_text and (
                any(t["level"] == 1 and t["title"].upper() == li_text.upper()
                    for t in toc_tree)
                or li_text.upper() == li_text  # convention L1 lines are ALL CAPS
            ):
                heading = ("", 1, li_text)
        if heading is None and toc_tree and tree_cursor == 0 and toc_tree[0]["level"] == 1:
            # The pandoc-merged first <ol> absorbed the section-1 heading
            # (it is NOT in the body); emit it before the first body
            # content so the section title leads its section.
            t = toc_tree[0]
            blocks.append({"tag": "h2", "el": c, "text": t["title"]})
            tree_cursor = 1
        if heading:
            _, level, title = heading
            # Emit un-emitted tree ancestors first (covers a level-1
            # heading pandoc merged into the source-TOC <ol>).
            while tree_cursor < len(toc_tree) and toc_tree[tree_cursor]["level"] < level:
                t = toc_tree[tree_cursor]
                blocks.append({"tag": _PTXT_HTML_TAG[t["level"]], "el": c,
                               "text": t["title"]})
                tree_cursor += 1
            if (tree_cursor < len(toc_tree)
                    and toc_tree[tree_cursor]["level"] == level
                    and toc_tree[tree_cursor]["title"].upper() == title.upper()):
                t = toc_tree[tree_cursor]
                blocks.append({"tag": _PTXT_HTML_TAG[level], "el": c,
                               "text": t["title"]})
                tree_cursor += 1
            else:
                blocks.append({"tag": _PTXT_HTML_TAG[level], "el": c, "text": title})
            continue
        blocks.append({"tag": tag, "el": c, "text": text})

    assign_numbers(blocks)

    parts = []
    toc = toc_plaintext(blocks)
    if toc:
        parts.append(toc)
    for b in blocks:
        tag, el, text = b["tag"], b["el"], b["text"]
        if tag == "imgph":
            parts.append(text)
        elif tag in HEADING_LEVEL:
            parts.append(heading_plaintext(tag, text, num=b["num"]))
        elif tag == "blockquote":
            parts.append("> " + text)
        elif tag == "ul":
            parts.append(_join_list_items(_render_list(el, ordered=False)))
        elif tag == "ol":
            parts.append(_join_list_items(_render_list(el, ordered=True)))
        elif tag == "hr":
            parts.append("---")
        elif tag == "table":
            parts.append("\n".join(_render_table(el)))
        else:
            parts.append(text)

    # Parts are separated by ONE blank line (an NBSP-only line: a literal
    # empty line pastes as TWO empty composer blocks on X). Blank lines go
    # between EVERY content element -- TOC, intro, headings, paragraphs,
    # lists -- while INSIDE a part everything stays tight: heading
    # rule/title/rule, TOC subheading groups, and list items are
    # single-spaced. This matches the user's manual edit of the sample
    # draft (2026-08-17): one blank between every body element.
    out = "\n\u00A0\n".join(parts)
    # Adjacent footnote markers in the body render comma-separated:
    # "claim. [1] [2]" -> "claim. [1], [2]". The end list is on its own
    # lines, so "]" + newline + "[" is never touched.
    out = re.sub(r"\] \[", "], [", out)
    fn = footnotes_plaintext(_footnotes_from_soup(soup))
    if fn:
        out = out + "\n\u00A0\n" + fn if out else fn
    return out


def essay_to_plaintext(html_path: str, image_placeholders: bool = False) -> str:
    """Convert a rendered essay HTML FILE to the full-document plaintext
    convention (see _plaintext_from_soup). image_placeholders=True emits
    "[IMAGE: <basename>]" parts at each skipped image's position."""
    soup = BeautifulSoup(Path(html_path).read_text(encoding="utf-8"), "html.parser")
    return _plaintext_from_soup(soup, image_placeholders=image_placeholders)


def html_to_plaintext(html_text: str, image_placeholders: bool = False) -> str:
    """Convert raw HTML TEXT (a page fragment or full document) to the
    full-document plaintext convention (see _plaintext_from_soup). Used
    by the htp CLI for inline text input."""
    return _plaintext_from_soup(
        BeautifulSoup(html_text, "html.parser"),
        image_placeholders=image_placeholders,
    )


def essay_to_tight_blocks(html_path: str, image_placeholders: bool = False,
                          target_chars: int = 8500) -> list[str]:
    """Convert a rendered essay HTML FILE to TIGHT paste blocks for X
    Articles (verified 2026-08-17): a list of single-block HTML clipboard
    payloads, each under the ~9KB autosave ceiling.

    X drops a single HTML block over ~9KB even though the Publish button
    enables; ~8.5KB chunks save. Each block pastes as ONE compact
    composer block (<br> soft returns, single spacing). Plaintext parts
    are grouped into ~target_chars chunks, EXCEPT every "[IMAGE: ...]"
    part becomes its own singleton block -- Insert>Media always appends
    AFTER the current block, so a placeholder must be a standalone block
    to receive an image at its position.

    Feed the blocks to the composer with an Enter between pastes."""
    plain = essay_to_plaintext(html_path, image_placeholders=image_placeholders)
    parts = plain.split("\n\u00A0\n")
    chunks, cur, cur_len = [], [], 0
    for p in parts:
        if p.startswith("[IMAGE: "):
            if cur:
                chunks.append("\n\u00A0\n".join(cur))
                cur, cur_len = [], 0
            chunks.append(p)
            continue
        # A single part can exceed target_chars when it is one long run with
        # no NBSP blank separators (e.g. the footnotes list: every citation
        # is a newline, not a blank line). Split such a part on newlines
        # into sub-parts that each fit, preserving line order.
        if len(p) > target_chars:
            sub = []
            for line in p.split("\n"):
                sub.append(line)
                if sum(len(x) + 3 for x in sub) > target_chars and len(sub) > 1:
                    chunk = "\n".join(sub[:-1])
                    if cur:
                        chunks.append("\n\u00A0\n".join(cur))
                        cur, cur_len = [], 0
                    chunks.append(to_paste_html(chunk))
                    sub = [sub[-1]]
            if sub:
                if cur:
                    chunks.append("\n\u00A0\n".join(cur))
                    cur, cur_len = [], 0
                cur.append("\n".join(sub))
                cur_len = len("\n".join(sub)) + 3
            continue
        add = len(p) + 3  # separator width
        if cur and cur_len + add > target_chars:
            chunks.append("\n\u00A0\n".join(cur))
            cur, cur_len = [], 0
        cur.append(p)
        cur_len += add
    if cur:
        chunks.append("\n\u00A0\n".join(cur))
    return [to_paste_html(c) for c in chunks]


def to_paste_html(plaintext: str) -> str:
    """Convert the plaintext convention output into a single-block HTML
    clipboard payload for X Articles.

    X's plain-text paste splits EVERY newline into its own paragraph
    block, which renders with excessive paragraph spacing (user-flagged
    2026-08-17). Pasting HTML with <br> soft returns keeps all lines in
    ONE block with compact single spacing; the NBSP indentation and
    NBSP-only blank lines pass through and render as indentation and
    single-height blank lines. HTML entities are escaped.
    """
    parts = []
    for line in plaintext.split("\n"):
        if line.strip() == "":
            # Blank line ("" or the converter's "\u00A0" blank) becomes
            # an extra <br>: exactly one empty line inside the block.
            parts.append("<br>")
        else:
            parts.append(html.escape(line) + "<br>")
    return "<div>" + "".join(parts) + "</div>"
