"""
Tests for publish/src/hydra-adapt-image

All tests run the script as a subprocess so the CLI contract is validated
exactly as a caller would experience it.
"""
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

SCRIPT = Path(__file__).parent.parent / 'hydra-adapt-image'

CONTEXTS = {
    'twitter-post':       (1200,  675),
    'twitter-article':    (1200,  480),
    'facebook-landscape': (1200,  630),
    'facebook-portrait':  (1080, 1350),
    'substack-note':      (1200,  675),
    'substack-cover':     (1200,  600),
    'nostr-short':        (1200,  675),
    'nostr-article':      (1200,  675),
}


def make_image(path: Path, w: int, h: int, color=(200, 100, 50)) -> Path:
    Image.new('RGB', (w, h), color).save(path)
    return path


def run(args: list, **kw):
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True, text=True, **kw
    )


# ---------------------------------------------------------------------------
# Dimensions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('context,expected', list(CONTEXTS.items()))
def test_output_dimensions(context, expected, tmp_path):
    src = make_image(tmp_path / 'src.jpg', 800, 600)
    out = tmp_path / f'{context}.jpg'
    r = run([str(src), context, '--out', str(out)])
    assert r.returncode == 0, r.stderr
    assert Image.open(out).size == expected


# ---------------------------------------------------------------------------
# Letterbox / pillarbox padding
# ---------------------------------------------------------------------------

def test_portrait_source_into_landscape_has_black_sides(tmp_path):
    """Tall white source → twitter-post canvas: left and right columns are black."""
    src = make_image(tmp_path / 'tall.png', 300, 900, color=(255, 255, 255))
    out = tmp_path / 'out.png'
    run([str(src), 'twitter-post', '--out', str(out)], check=True)
    img = Image.open(out)
    assert img.size == (1200, 675)
    # Both horizontal edges should be black padding
    assert img.getpixel((0,   337)) == (0, 0, 0), 'left edge not black'
    assert img.getpixel((1199, 337)) == (0, 0, 0), 'right edge not black'


def test_landscape_source_into_portrait_has_black_top_bottom(tmp_path):
    """Wide green source → facebook-portrait canvas: top and bottom rows are black."""
    src = make_image(tmp_path / 'wide.png', 1200, 200, color=(0, 200, 0))
    out = tmp_path / 'out.png'
    run([str(src), 'facebook-portrait', '--out', str(out)], check=True)
    img = Image.open(out)
    assert img.size == (1080, 1350)
    assert img.getpixel((540,    0)) == (0, 0, 0), 'top not black'
    assert img.getpixel((540, 1349)) == (0, 0, 0), 'bottom not black'


def test_source_content_visible_in_center(tmp_path):
    """Red square source into landscape context: center pixel should be red."""
    src = make_image(tmp_path / 'red.png', 675, 675, color=(255, 0, 0))
    out = tmp_path / 'out.png'
    run([str(src), 'twitter-post', '--out', str(out)], check=True)
    r, g, b = Image.open(out).getpixel((600, 337))
    assert r > 200 and g < 50 and b < 50, f'expected red center, got ({r},{g},{b})'


def test_exact_aspect_ratio_source_fills_canvas(tmp_path):
    """Source image that already matches the target ratio: no padding."""
    src = make_image(tmp_path / 'exact.png', 1200, 480, color=(0, 0, 200))
    out = tmp_path / 'out.png'
    run([str(src), 'twitter-article', '--out', str(out)], check=True)
    img = Image.open(out)
    # Corners should not be black (no padding needed)
    for px in [(0, 0), (1199, 0), (0, 479), (1199, 479)]:
        r, g, b = img.getpixel(px)
        assert b > 100, f'corner {px} unexpectedly black: ({r},{g},{b})'


# ---------------------------------------------------------------------------
# Format selection
# ---------------------------------------------------------------------------

def test_facebook_context_defaults_to_jpg(tmp_path):
    src = make_image(tmp_path / 'src.png', 800, 600)
    out = tmp_path / 'out.jpg'
    run([str(src), 'facebook-landscape', '--out', str(out)], check=True)
    assert Image.open(out).format == 'JPEG'


def test_twitter_png_source_defaults_to_png(tmp_path):
    src = make_image(tmp_path / 'src.png', 800, 600)
    out = tmp_path / 'out.png'
    run([str(src), 'twitter-post', '--out', str(out)], check=True)
    assert Image.open(out).format == 'PNG'


def test_format_override_jpg(tmp_path):
    src = make_image(tmp_path / 'src.png', 800, 600)
    out = tmp_path / 'out.jpg'
    run([str(src), 'twitter-post', '--out', str(out), '--format', 'jpg'], check=True)
    assert Image.open(out).format == 'JPEG'


def test_format_override_png_for_facebook(tmp_path):
    src = make_image(tmp_path / 'src.jpg', 800, 600)
    out = tmp_path / 'out.png'
    run([str(src), 'facebook-landscape', '--out', str(out), '--format', 'png'], check=True)
    assert Image.open(out).format == 'PNG'


# ---------------------------------------------------------------------------
# Default output path
# ---------------------------------------------------------------------------

def test_default_output_path_naming(tmp_path):
    src = make_image(tmp_path / 'src.jpg', 400, 300)
    r = run([str(src), 'twitter-article'])
    assert r.returncode == 0, r.stderr
    out = Path(r.stdout.strip())
    assert out.name == 'hydra-img-twitter-article.jpg'
    assert out.exists()


# ---------------------------------------------------------------------------
# "all" context
# ---------------------------------------------------------------------------

# Contexts produced by "all" (excludes the _ALL_SKIP aliases)
_ALL_EXPECTED = {
    'twitter-post':       (1200,  675),
    'twitter-article':    (1200,  480),
    'facebook-landscape': (1200,  630),
    'facebook-portrait':  (1080, 1350),
    'substack-cover':     (1200,  600),
}


def test_all_generates_expected_files(tmp_path):
    src = make_image(tmp_path / 'src.png', 1280, 720)
    r = run([str(src), 'all', '--out-dir', str(tmp_path)])
    assert r.returncode == 0, r.stderr
    outputs = {p.name: p for p in tmp_path.glob('src-*')}
    for ctx, (w, h) in _ALL_EXPECTED.items():
        # find file matching this context regardless of extension
        matches = [n for n in outputs if f'-{ctx}-' in n]
        assert matches, f'No output found for context {ctx!r}; files: {list(outputs)}'
        img = Image.open(outputs[matches[0]])
        assert img.size == (w, h), f'{ctx}: expected {w}x{h}, got {img.size}'


def test_all_skips_duplicate_16x9_contexts(tmp_path):
    src = make_image(tmp_path / 'src.png', 1280, 720)
    run([str(src), 'all', '--out-dir', str(tmp_path)], check=True)
    outputs = list(tmp_path.glob('src-*'))
    names = [p.name for p in outputs]
    for skipped in ('substack-note', 'nostr-short', 'nostr-article'):
        assert not any(skipped in n for n in names), \
            f'Skipped context {skipped!r} appeared in output: {names}'


def test_all_output_naming_convention(tmp_path):
    src = make_image(tmp_path / 'cover.jpg', 1280, 720)
    run([str(src), 'all', '--out-dir', str(tmp_path)], check=True)
    outputs = list(tmp_path.glob('cover-*'))
    assert len(outputs) == len(_ALL_EXPECTED)
    for p in outputs:
        # name must be cover-{context}-{W}x{H}.{ext}
        assert p.name.startswith('cover-'), p.name
        parts = p.stem.split('-')
        # last part should be WxH
        assert 'x' in parts[-1], f'Expected WxH suffix in {p.name}'


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

def test_missing_source_exits_nonzero(tmp_path):
    r = run([str(tmp_path / 'does_not_exist.jpg'), 'twitter-post'])
    assert r.returncode != 0


def test_invalid_context_exits_nonzero(tmp_path):
    src = make_image(tmp_path / 'src.jpg', 400, 300)
    r = run([str(src), 'not-a-real-platform'])
    assert r.returncode != 0
