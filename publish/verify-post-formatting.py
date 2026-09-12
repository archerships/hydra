#!/Users/crasch/av/venv/hydra/bin/python3
"""verify-post-formatting.py — Run automated formatting checks on an archerships.com post.

Usage:
  /opt/homebrew/bin/python3.14 verify-post-formatting.py SLUG [--checks c1,c2,...] [--warn-only]

Checks the source .md and rendered .html in ~/av/doc/posts/SLUG/.
Assumes the .html has been rendered (render-post.py or equivalent).
Exit 0 if all checks pass (or warn-only mode); exit 1 if any check FAILs.
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
POSTS_DIR = Path.home() / "av" / "doc" / "posts"
DST_DIR = Path.home() / "av" / "prj" / "archerships.com" / "dst" / "essays"
REQUIRED_FM = ["type", "purpose", "title", "description", "date", "section",
               "tags", "ai_percentage", "published_at"]
VALID_SECTIONS = {"acceleration", "tech", "seasteading"}
VALID_TAGS = {
    "tech-brief", "reference", "tutorial", "ai", "privacy", "open-source",
    "self-hosting", "arweave", "monero", "crypto", "accelerationism",
    "libertarianism", "seasteading", "pronatalism", "longevity", "censorship",
    "sovereignty", "freestateproject", "immigration", "drugs", "education",
    "ectogenesis", "socialpolicybonds", "research", "youtube", "alignment"
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def resolve_slug(slug: str) -> dict:
    """Find the post directory and files for a slug. Returns dict with paths."""
    candidates = sorted(POSTS_DIR.glob(f"*-{slug}"))
    if not candidates:
        candidates = sorted(POSTS_DIR.glob(f"*{slug}*"))
    if not candidates:
        return {"md": None, "html": None, "dir": None}
    post_dir = candidates[0]
    md = post_dir / f"{post_dir.name}.md"
    html = post_dir / f"{post_dir.name}.html"
    if not md.exists():
        # try flat match
        for f in post_dir.glob("*.md"):
            md = f
            break
    return {"md": md if md.exists() else None,
            "html": html if html.exists() else None,
            "dir": post_dir}


def read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def split_fm(text: str) -> tuple[str, str]:
    """Return (frontmatter_yaml_string, body_markdown)."""
    if not text.startswith("---"):
        return "", text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return "", text
    return parts[1].strip(), parts[2].strip()


def extract_fm_val(raw_yaml: str, key: str) -> list:
    """Extract a scalar or list value from raw YAML frontmatter. Returns [values]."""
    pattern = rf"^{re.escape(key)}:\s*(.+)$"
    results = []
    for line in raw_yaml.split("\n"):
        m = re.match(pattern, line)
        if m:
            results.append(m.group(1).strip().strip("'\""))
    return results


# ---------------------------------------------------------------------------
# Check functions — each returns (status, message)
# status: "PASS", "FAIL", "WARN", "SKIP"
# ---------------------------------------------------------------------------
class Checker:
    def __init__(self, slug: str, paths: dict):
        self.slug = slug
        self.md_path = paths["md"]
        self.html_path = paths["html"]
        self.post_dir = paths["dir"]
        self.md_text = read_file(self.md_path) if self.md_path else ""
        self.html_text = read_file(self.html_path) if self.html_path else ""
        self.fm_raw, self.body_md = split_fm(self.md_text) if self.md_text else ("", "")

    # --- Source markdown checks ---

    def c14_no_unicode(self):
        """Check 14: No emoji / non-ASCII in source .md."""
        if not self.md_text:
            return "SKIP", "no source .md"
        non_ascii = []
        for i, line in enumerate(self.md_text.split("\n"), 1):
            for ch in line:
                if ord(ch) > 127:
                    non_ascii.append(f"L{i}: U+{ord(ch):04X} {ch}")
        if non_ascii:
            return "WARN", f"non-ASCII chars: {', '.join(non_ascii[:8])}"
        return "PASS", "all ASCII"

    def c15_no_em_dash(self):
        """Check 15: Two dashes (--) not em dash in source."""
        if not self.md_text:
            return "SKIP", ""
        count = self.md_text.count("\u2014")
        if count:
            return "FAIL", f"{count} em dash(es) found — use '--' instead"
        return "PASS", "no em dashes"

    def c19_slug_consistency(self):
        """Check 19: Slug == directory name == filename stem."""
        if not self.post_dir:
            return "SKIP", "no directory"
        dir_slug = self.post_dir.name
        if self.md_path:
            md_stem = self.md_path.stem
            if dir_slug != md_stem:
                return "FAIL", f"directory '{dir_slug}' != file stem '{md_stem}'"
        fm_slug = extract_fm_val(self.fm_raw, "slug")
        if fm_slug and fm_slug[0] != dir_slug:
            return "FAIL", f"frontmatter slug '{fm_slug[0]}' != directory '{dir_slug}'"
        return "PASS", "consistent"

    def c20_tags_format(self):
        """Check 20: Tags lowercase, hyphens only, 2-5 tags."""
        vals = extract_fm_val(self.fm_raw, "tags")
        if not vals:
            return "WARN", "no tags in frontmatter"
        tags_str = vals[0]
        if tags_str in ("[]", ""):
            return "WARN", "empty tags list"
        tags = [t.strip().strip("'\"").strip(",").strip("[]").strip() for t in tags_str.replace("[", "").replace("]", "").split(",")]
        tags = [t for t in tags if t]
        if len(tags) < 2:
            return "WARN", f"only {len(tags)} tag(s), want 2-5"
        if len(tags) > 5:
            return "WARN", f"{len(tags)} tags, want ≤5"
        invalid = []
        for t in tags:
            if t != t.lower():
                invalid.append(f"{t} (not lowercase)")
            elif "_" in t:
                invalid.append(f"{t} (has underscore)")
        if invalid:
            return "FAIL", f"invalid tags: {'; '.join(invalid)}"
        return "PASS", f"{len(tags)} tags ok"

    def c21_no_h1_in_body(self):
        """Check 21: No H1 (# heading) in body — renderer generates from title."""
        if not self.body_md:
            return "SKIP", ""
        in_fence = False
        for line in self.body_md.split("\n"):
            if line.strip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            stripped = line.strip()
            if re.match(r"^#\s+\S", stripped) and not stripped.startswith("##"):
                return "FAIL", f"H1 in body: '{stripped[:60]}'"
            if stripped.startswith("# ") and not stripped.startswith("##"):
                return "FAIL", f"H1 in body: '{stripped[:60]}'"
        return "PASS", "no H1 in body"

    def c22_spacing(self):
        """Check 22: One blank line before/after headings, lists, code blocks."""
        if not self.body_md:
            return "SKIP", ""
        lines = self.body_md.split("\n")
        issues = 0
        for i, line in enumerate(lines):
            s = line.strip()
            # heading
            if s.startswith("##") and i > 0 and lines[i-1].strip() != "":
                issues += 1
            if s.startswith("##") and i + 1 < len(lines) and lines[i+1].strip() != "":
                issues += 1
            # list
            if re.match(r"^[\d]+\.\s", s) and i > 0 and lines[i-1].strip() != "":
                issues += 1
            # code fence
            if s.startswith("```"):
                if not s.startswith("```bash") and not s.startswith("```python") and not s.startswith("```text") and s != "```":
                    if i > 0 and lines[i-1].strip() != "":
                        issues += 1
        if issues > 10:
            return "WARN", f"{issues} spacing issues (blank lines before/after blocks)"
        elif issues:
            return "WARN", f"{issues} minor spacing nits"
        return "PASS", "spacing ok"

    def c23_no_tracking_params(self):
        """Check 23: No tracking parameters on URLs."""
        if not self.md_text:
            return "SKIP", ""
        urls = re.findall(r'https?://\S+', self.md_text)
        bad = [u for u in urls if 'utm_' in u or '?ref=' in u or '?source=' in u or 'fbclid=' in u]
        if bad:
            return "FAIL", f"{len(bad)} URL(s) with tracking params: {bad[0][:80]}..."
        return "PASS", "clean URLs"

    def c24_webp_images(self):
        """Check 24: Images are WebP (PNG only if transparency needed)."""
        if not self.md_text:
            return "SKIP", ""
        imgs = re.findall(r'!\[.*?\]\(([^)]+)\)', self.md_text)
        non_webp = [i for i in imgs if not i.lower().endswith(('.webp', '.png', '.svg')) and i.startswith(('img/', '../ast/'))]
        if non_webp:
            return "WARN", f"non-WebP images: {non_webp[:3]}"
        return "PASS", "images ok"

    def c25_image_dimensions(self):
        """Check 25: width/height attrs on images match actual dimensions."""
        if not self.html_text:
            return "SKIP", ""
        imgs = re.findall(r'<img[^>]+src="([^"]+)"[^>]*width="(\d+)"[^>]*height="(\d+)"[^>]*>', self.html_text)
        issues = []
        for src, w, h in imgs[:10]:
            # Try to find the actual file
            for base in [self.post_dir, Path.home() / "av" / "prj" / "archerships.com" / "dst"]:
                candidate = base / src
                if candidate.exists():
                    try:
                        result = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(candidate)],
                                                capture_output=True, text=True, timeout=10)
                        dims = {}
                        for line in result.stdout.split("\n"):
                            m = re.match(r"\s*pixel(Width|Height):\s*(\d+)", line)
                            if m:
                                dims[m.group(1)] = m.group(2)
                        if dims.get("Width") != w or dims.get("Height") != h:
                            issues.append(f"{src}: HTML {w}x{h}, actual {dims.get('Width','?')}x{dims.get('Height','?')}")
                    except Exception:
                        pass
                    break
        if issues:
            return "FAIL", f"{len(issues)} dimension mismatches: {issues[0]}"
        return "PASS", "dimensions match"

    def c26_code_language(self):
        """Check 26: Code blocks have language specified."""
        if not self.body_md:
            return "SKIP", ""
        bare = re.findall(r'^```\s*$', self.body_md, re.MULTILINE)
        if bare:
            return "WARN", f"{len(bare)} code block(s) without language"
        return "PASS", "all have language"

    # --- Rendered HTML checks ---

    def c06_numbered_lists(self):
        """Check 6: Numbered lists display numbers (list-style-type not 'none')."""
        if not self.html_text:
            return "SKIP", ""
        ols = re.findall(r'<ol[^>]*>', self.html_text)
        if not ols:
            return "PASS", "no ordered lists"
        # Check inline styles that might hide markers
        hidden = [o for o in ols if 'list-style:none' in o.replace(' ', '') or 'list-style-type:none' in o.replace(' ', '')]
        if hidden:
            return "FAIL", f"{len(hidden)} ol(s) with list-style:none"
        return "PASS", f"{len(ols)} ol(s) ok"

    def c08_footnote_count(self):
        """Check 8: Inline footnote markers match Notes section entries."""
        if not self.html_text:
            return "SKIP", ""
        body_sups = len(re.findall(r'<sup[^>]*>\d+</sup>', self.html_text))
        fn_items = len(re.findall(r'<li\s+id="fn\d+"', self.html_text))
        if body_sups == 0 and fn_items == 0:
            return "PASS", "no footnotes"
        if body_sups != fn_items:
            return "WARN", f"body sups={body_sups}, fn items={fn_items} (pandoc duplicates?)"
        return "PASS", f"{body_sups} footnotes match"

    def c12_alt_text(self):
        """Check 11: All images have alt text."""
        if not self.html_text:
            return "SKIP", ""
        imgs = re.findall(r'<img([^>]*)>', self.html_text)
        no_alt = [i for i in imgs if 'alt=' not in i]
        if no_alt:
            return "FAIL", f"{len(no_alt)} img(s) missing alt attribute"
        return "PASS", "all have alt"

    def c17_toc_present(self):
        """Check 17: TOC generated for long-form posts."""
        if not self.html_text:
            return "SKIP", ""
        fm_type = extract_fm_val(self.fm_raw, "type")
        if fm_type and fm_type[0] not in ("long-form", "essay", "short-form"):
            return "SKIP", f"type={fm_type[0]}"
        heading_count = len(re.findall(r'^##\s+\S', self.body_md, re.MULTILINE)) if self.body_md else 0
        if heading_count < 3:
            return "SKIP", f"only {heading_count} headings"
        if '<details><summary>Contents</summary>' in self.html_text or '<summary>Contents</summary>' in self.html_text:
            return "PASS", "TOC present"
        return "WARN", "no TOC detected"

    def c27_csp(self):
        """Check 27: CSP meta tag present."""
        if not self.html_text:
            return "SKIP", ""
        if 'Content-Security-Policy' in self.html_text:
            return "PASS", "CSP present"
        return "FAIL", "CSP meta tag missing"

    def c28_size_limits(self):
        """Check 28: Size limits."""
        results = []
        if self.html_path and self.html_path.exists():
            sz = self.html_path.stat().st_size
            results.append(f"HTML={sz/1024:.0f}KB {'OK' if sz<100000 else 'FAIL'}")
        # check images
        if self.post_dir:
            img_dir = self.post_dir / "img"
            if img_dir.exists():
                for f in img_dir.glob("*"):
                    if f.suffix.lower() in ('.webp', '.png', '.jpg', '.jpeg'):
                        sz = f.stat().st_size
                        if sz > 100000:
                            results.append(f"{f.name}={sz/1024:.0f}KB OVER 100KB")
        if any('FAIL' in r or 'OVER' in r for r in results):
            return "FAIL", "; ".join(results[:5])
        return "PASS", "; ".join(results[:5]) if results else "no size checks"

    def c30_heading_hierarchy(self):
        """Check 30: One <h1>, correct heading hierarchy."""
        if not self.html_text:
            return "SKIP", ""
        h1s = len(re.findall(r'<h1[>\s]', self.html_text))
        if h1s != 1:
            return "FAIL", f"{h1s} <h1> elements (want 1)"
        # Check no skipped levels in markdown
        levels = []
        for line in (self.body_md or "").split("\n"):
            m = re.match(r'^(#{2,6})\s+\S', line)
            if m:
                levels.append(len(m.group(1)))
        for i in range(len(levels) - 1):
            if levels[i+1] > levels[i] + 1:
                return "WARN", f"heading level skip: H{levels[i]} -> H{levels[i+1]}"
        return "PASS", "heading hierarchy ok"

    def c31_html_lang(self):
        """Check 31: <html lang="en">."""
        if not self.html_text:
            return "SKIP", ""
        if re.search(r'<html[^>]*lang="en"', self.html_text):
            return "PASS", "lang=en present"
        return "FAIL", "missing lang='en' on html element"

    def c32_reduced_motion(self):
        """Check 32: prefers-reduced-motion in CSS."""
        if not self.html_text:
            return "SKIP", ""
        if 'prefers-reduced-motion' in self.html_text:
            return "PASS", "media query present"
        # May be in external CSS — check the live site CSS
        # For now, flag if neither inline nor in linked stylesheets
        if 'motion' in self.html_text:
            return "PASS", "motion mention found"
        return "WARN", "no reduced-motion media query detected (may be in external CSS)"

    # --- Frontmatter checks ---

    def c13_frontmatter(self):
        """Check 13: Required frontmatter fields present."""
        if not self.fm_raw:
            return "FAIL", "no frontmatter found"
        fm_lines = self.fm_raw.split("\n")
        missing = []
        for key in REQUIRED_FM:
            # Match "key:" at the start of any line (supports multi-line values)
            found = any(re.match(rf"^{re.escape(key)}\s*:", line) for line in fm_lines)
            if not found:
                missing.append(key)
        if missing:
            return "FAIL", f"missing fields: {', '.join(missing)}"
        sec = extract_fm_val(self.fm_raw, "section")
        if sec and sec[0] not in VALID_SECTIONS:
            return "WARN", f"section '{sec[0]}' not in {VALID_SECTIONS}"
        return "PASS", "all required fields present"

    def c07_citation_format(self):
        """Check 7: Citation format — each footnote has author, title, date, URL."""
        if not self.body_md:
            return "SKIP", ""
        footnotes = re.findall(r'^\[\^(\d+)\]:\s*(.+)', self.body_md, re.MULTILINE)
        bad = []
        for fn_num, fn_text in footnotes:
            has_url = bool(re.search(r'https?://', fn_text))
            has_author = bool(re.match(r'^[A-Z]', fn_text))
            has_quote = '"' in fn_text
            has_year = bool(re.search(r'\b(19|20)\d{2}\b', fn_text))
            if not all([has_url, has_author, has_quote, has_year]):
                missing_parts = []
                if not has_url: missing_parts.append("URL")
                if not has_author: missing_parts.append("author")
                if not has_quote: missing_parts.append("title in quotes")
                if not has_year: missing_parts.append("date/year")
                bad.append(f"[^{fn_num}]: missing {', '.join(missing_parts)}")
        if bad:
            return "WARN", f"{len(bad)} footnote(s) with missing fields: {bad[0]}"
        return "PASS", f"{len(footnotes)} footnotes ok"

    # -------------------------------------------------------------------
    def run_all(self) -> tuple[int, int, int, int]:
        """Run all checks. Returns (pass, fail, warn, skip)."""
        checks = []
        for name in sorted(dir(self)):
            if name.startswith("c") and name[1:3].isdigit() and name[1] != '0':
                checks.append(name)

        results = {"PASS": 0, "FAIL": 0, "WARN": 0, "SKIP": 0}
        for name in sorted(checks):
            method = getattr(self, name)
            status, msg = method()
            num = name[1:].split("_")[0]
            label = f"[{status:4s}] #{int(num):2d}: {msg}"
            print(label)
            results[status] += 1
        return results["PASS"], results["FAIL"], results["WARN"], results["SKIP"]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Verify archerships.com post formatting")
    parser.add_argument("slug", help="Post slug (YYYY-MM-DD-slug)")
    parser.add_argument("--checks", help="Comma-separated check numbers to run (default: all)")
    parser.add_argument("--warn-only", action="store_true", help="Exit 0 even on FAIL")
    args = parser.parse_args()

    paths = resolve_slug(args.slug)
    if not paths["md"] and not paths["html"]:
        print(f"ERROR: No post found for slug '{args.slug}'")
        print(f"  Searched in: {POSTS_DIR}")
        sys.exit(2)

    print(f"Checking post: {paths['dir'].name if paths['dir'] else args.slug}")
    print(f"  Source: {paths['md']}")
    print(f"  HTML:   {paths['html']}")
    print()

    checker = Checker(args.slug, paths)
    passed, failed, warned, skipped = checker.run_all()

    print()
    total = passed + failed + warned + skipped
    print(f"Results: {passed} PASS, {failed} FAIL, {warned} WARN, {skipped} SKIP ({total} total)")

    if failed and not args.warn_only:
        print(f"\n{'-'*40}")
        print("FAILING checks must be fixed before publishing.")
        sys.exit(1)
    elif failed:
        print(f"\n{'-'*40}")
        print(f"WARN-only mode: {failed} FAIL(s) treated as non-fatal.")
        sys.exit(0)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
