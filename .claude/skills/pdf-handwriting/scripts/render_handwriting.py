"""Render Chinese text as simulated pen handwriting and overlay it onto a PDF
at exact rectangles, leaving all other page content untouched.

Alternative approach (not used here, documented for future reference): PyMuPDF
can insert real vector text via `page.insert_font()` + `page.insert_text(...,
morph=(point, Matrix.rotate(angle)))` per glyph, which keeps the result as
selectable/searchable text instead of a flattened image. That path yields a
smaller file and crisper zoom, but per-glyph morph loops are fiddly to get
right and give less direct control over jitter/ink texture. The raster
(Pillow) approach below is simpler to tune and is the recommended default.

Usage:
    from render_handwriting import overlay_handwritten_text
    overlay_handwritten_text(
        "input.pdf", "output.pdf",
        placements=[
            {"page": 0, "text": "...", "box": (x, y, w, h)},
            ...
        ],
    )
"""

import io
import math
import random
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFont, ImageFilter

DEFAULT_FONT_PATH = str(Path(__file__).resolve().parent.parent / "assets" / "fonts" / "ZhiMangXing-Regular.ttf")
LEGIBLE_FALLBACK_FONT_PATH = str(Path(__file__).resolve().parent.parent / "assets" / "fonts" / "LXGWWenKai-Regular.ttf")
# Black gel/rollerball pen (黑色签字笔) — explicit user requirement; the
# per-glyph alpha jitter in _render_glyph still gives natural ink variation.
DEFAULT_INK_COLOR = (0, 0, 0)

# Normal human handwriting size, fixed regardless of how tall the target box
# is — explicit user requirement ("不要因为框框的大小而改变字号"). 19pt matches
# the size the user approved on the second page's single-line fields.
DEFAULT_FONT_SIZE_PT = 19

# Hard cap on wrapped lines (用户要求最多写两排). If text can't fit in this
# many lines at DEFAULT_FONT_SIZE_PT, the size steps down (never below
# MIN_FONT_SIZE_PT) until it does, rather than silently dropping characters.
DEFAULT_MAX_LINES = 2
# Floor for the step-down. Kept low (8pt) so this skill can also fill small
# cells on dense forms (e.g. 分部工程质量评定表, whose machine text is ~8pt) by
# passing a small preferred font_size — it does not affect large-cell docs
# that pass preferred=19 and fit within max_lines without ever stepping down.
MIN_FONT_SIZE_PT = 8

# Stroke thinning: erode each glyph's alpha by this many supersampled pixels
# so the running-script strokes read a touch thinner than the font's native
# weight. Kept MILD (0.3): round-3 asked for finer strokes, but pushing it to
# 1.0 made the ink faint/"不明显" — the strokes must stay clearly visible,
# solid black. 0 = the font's own (heavier) weight. Tune per doc by eye.
DEFAULT_STROKE_THINNING = 0.3

_PUNCT_NO_LINE_START = set("，。、；：！？」』】)>》")


# Fraction of box width used as the wrap budget. The rendered line can never
# exceed the planned nominal width by more than the max per-character advance
# multiplier in compose_field_image's spacing jitter (uniform(0.86, 1.02),
# mean ~0.94 — characters sit at or tighter than nominal width to look like
# a connected running hand), so this fraction may go up to ~0.95 before a
# worst-case jitter roll could push a line past the box edge. 0.95 is needed
# to fit the standard two-line review comment at the fixed 19pt size; the
# typical (mean-jitter) rendered line still ends well short of the border.
_WRAP_WIDTH_FRACTION = 0.95


def _char_advance(ch, font, draw):
    """Nominal (un-jittered) advance width for one character — the single
    source of truth shared by wrap planning and actual rendering, so the two
    can never disagree about how much horizontal space a line needs."""
    if ch == " ":
        return font.size * 0.5
    return draw.textlength(ch, font=font)


def _wrap_chars(text, font, max_width_px, draw):
    """Greedily wrap `text` per-character (no word boundaries in Chinese) so
    each line's nominal rendered width stays under max_width_px. Pulls
    punctuation that would start a line back onto the previous line."""
    lines = []
    current = ""
    current_width = 0.0
    for ch in text:
        w = _char_advance(ch, font, draw)
        if current and current_width + w > max_width_px:
            lines.append(current)
            current = ch
            current_width = w
        else:
            current += ch
            current_width += w
    if current:
        lines.append(current)
    for i in range(1, len(lines)):
        while lines[i] and lines[i][0] in _PUNCT_NO_LINE_START and lines[i - 1]:
            lines[i - 1] += lines[i][0]
            lines[i] = lines[i][1:]
    return [l for l in lines if l]


def fit_font_size_and_wrap(text, box_w_pt, box_h_pt, font_path=DEFAULT_FONT_PATH,
                            preferred_size=DEFAULT_FONT_SIZE_PT, min_size=MIN_FONT_SIZE_PT,
                            max_lines=DEFAULT_MAX_LINES, dpi_scale=6, line_height_mult=1.6):
    """Wrap `text` at a FIXED preferred font size (normal handwriting size —
    the size never grows to fill a tall box). Only if the text cannot fit in
    `max_lines` at that size does the size step down, stopping at `min_size`
    (at which point the max_lines cap yields to not losing characters).
    Returns (font_size_pt, list_of_lines)."""
    tmp = Image.new("RGBA", (10, 10))
    draw = ImageDraw.Draw(tmp)
    box_w_px = box_w_pt * dpi_scale
    for size in range(preferred_size, min_size - 1, -1):
        font = ImageFont.truetype(font_path, int(size * dpi_scale))
        lines = _wrap_chars(text, font, box_w_px * _WRAP_WIDTH_FRACTION, draw)
        if len(lines) <= max_lines:
            return size, lines
    font = ImageFont.truetype(font_path, int(min_size * dpi_scale))
    lines = _wrap_chars(text, font, box_w_px * _WRAP_WIDTH_FRACTION, draw)
    return min_size, lines


def _shear_image(img, shear):
    """Apply a horizontal shear (italic-style slant) to a transparent glyph
    image, expanding the canvas so nothing gets clipped. Positive shear
    leans the top to the right, matching a natural right-leaning pen slant."""
    if abs(shear) < 1e-3:
        return img
    w, h = img.size
    extra = int(abs(shear) * h)
    return img.transform(
        (w + extra, h), Image.AFFINE,
        (1, shear, -extra if shear > 0 else 0, 0, 1, 0),
        resample=Image.BICUBIC,
    )


def _thin_alpha(img, thinning):
    """Erode the glyph's alpha channel by ~`thinning` supersampled pixels so
    the ink strokes read thinner than the font's native weight, without
    touching the RGB (ink color). MinFilter shrinks the opaque region; a
    fractional remainder is applied as a partial (blended) erosion so the
    strength is smoothly tunable rather than jumping a whole pixel at a time."""
    if thinning <= 0:
        return img
    r, g, b, a = img.split()
    whole = int(thinning)
    for _ in range(whole):
        a = a.filter(ImageFilter.MinFilter(3))
    frac = thinning - whole
    if frac > 1e-3:
        eroded = a.filter(ImageFilter.MinFilter(3))
        a = Image.blend(a, eroded, frac)
    return Image.merge("RGBA", (r, g, b, a))


def _render_glyph(ch, font, ink_color, rng, base_shear=0.18, thinning=0.0):
    """Render a single character to its own padded transparent image with
    random rotation and italic slant, simulating a real running/cursive hand
    rather than isolated print-stamp characters. `thinning` erodes the stroke
    weight (see _thin_alpha); pen-pressure is conveyed by per-glyph alpha
    jitter alone (no stroke_width thickening pass — that read as too heavy)."""
    bbox = font.getbbox(ch)
    w = max(1, bbox[2] - bbox[0])
    h = max(1, bbox[3] - bbox[1])
    pad = int(max(w, h) * 0.5) + 4
    img = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # keep ink solid/dark (small alpha jitter only) so strokes stay prominent —
    # too much transparency reads as faint/washed-out ("不明显").
    alpha = rng.randint(238, 255)
    draw.text((pad - bbox[0], pad - bbox[1]), ch, font=font, fill=ink_color + (alpha,))
    img = _thin_alpha(img, thinning)
    # rotation + slant vary per glyph; an occasional glyph tips noticeably more
    # (nobody writes every character at the same angle).
    angle = rng.uniform(-7.0, 7.0)
    if rng.random() < 0.15:
        angle += rng.uniform(-6.0, 6.0)
    img = img.rotate(angle, resample=Image.BICUBIC, expand=True)
    shear = base_shear + rng.uniform(-0.11, 0.11)
    return _shear_image(img, shear)


def compose_field_image(lines, font_path, font_size_pt, box_w_pt, box_h_pt,
                         ink_color=DEFAULT_INK_COLOR, dpi_scale=6, rng=None,
                         line_height_mult=1.6, thinning=DEFAULT_STROKE_THINNING):
    """Lay characters out along a slightly wavy per-line baseline with
    independent per-character jitter (rotation, italic slant, vertical
    offset, scale, stroke weight, spacing) and tight/slightly-overlapping
    spacing so adjacent characters visually flow into each other like a
    running hand, rather than reading as isolated stamped glyphs. Returns an
    RGBA image sized box_w_pt*dpi_scale x box_h_pt*dpi_scale (transparent
    background) ready to overlay on a PDF box."""
    rng = rng or random.Random()
    box_w_px = int(box_w_pt * dpi_scale)
    box_h_px = int(box_h_pt * dpi_scale)
    canvas = Image.new("RGBA", (box_w_px, box_h_px), (0, 0, 0, 0))
    font_px = int(font_size_pt * dpi_scale)
    font = ImageFont.truetype(font_path, font_px)

    line_height = font_px * line_height_mult
    total_text_height = line_height * len(lines)
    # Top-aligned, not vertically centered: with a fixed handwriting size a
    # tall box holds fewer lines than it "could," and a real person starts
    # writing from the first line under the label — centering would float
    # the text oddly in the middle of a large empty cell.
    top_margin = min(line_height * 0.2, max(0.0, (box_h_px - total_text_height) / 2))
    left_margin = font_px * 0.12

    draw_measure = ImageDraw.Draw(canvas)
    for li, line in enumerate(lines):
        baseline_y = top_margin + li * line_height + line_height * 0.72
        x = left_margin
        wave_phase = rng.uniform(0, 2 * math.pi)
        # A real hand doesn't keep a perfectly horizontal baseline — give each
        # line its own small slope and a slightly different starting indent so
        # no two lines/pages look stamped from a template. CRITICAL: the indent
        # is bounded by this line's actual free space so it can NEVER push the
        # line past the box's right edge (which would clip the tail or shove it
        # across a cell border). A near-full line therefore gets ~0 indent; only
        # lines with real slack (short lines) get a visible position shift.
        slope = rng.uniform(-0.05, 0.05)
        line_nominal = sum(_char_advance(c, font, draw_measure) for c in line)
        slack = box_w_px - line_nominal - left_margin
        max_indent = max(0.0, min(0.55 * font_px, slack - 0.03 * box_w_px))
        x += rng.uniform(0.0, max_indent)
        for ch in line:
            nominal_advance = _char_advance(ch, font, draw_measure)
            if ch == " ":
                x += nominal_advance
                continue
            glyph_img = _render_glyph(ch, font, ink_color, rng, thinning=thinning)
            # per-character size variation, with an occasional larger/smaller
            # outlier (real writing isn't uniform — some strokes run big).
            scale = rng.uniform(0.90, 1.15)
            if rng.random() < 0.12:
                scale *= rng.uniform(0.82, 1.22)
            if abs(scale - 1.0) > 1e-3:
                new_size = (max(1, int(glyph_img.width * scale)), max(1, int(glyph_img.height * scale)))
                glyph_img = glyph_img.resize(new_size, Image.LANCZOS)
            wave_offset = math.sin(x / (font_px * 2.2) + wave_phase) * font_px * 0.08
            slope_offset = slope * (x - left_margin)
            jitter_y = rng.uniform(-0.08, 0.08) * font_px
            # center the (padded, possibly larger) glyph image on the cursor
            # column so ink can overflow its own cell slightly (natural for
            # handwriting) without perturbing the cursor's advance math.
            paste_x = int(x - (glyph_img.width - nominal_advance) / 2)
            paste_y = int(baseline_y - glyph_img.height * 0.72 + wave_offset + slope_offset + jitter_y)
            _safe_alpha_composite(canvas, glyph_img, paste_x, paste_y, box_w_px, box_h_px)
            # advance by the nominal width pulled slightly tighter than 1.0 on
            # average (characters overlap a touch, like a running hand) plus
            # jitter — this MUST stay close to the width budget used by
            # _wrap_chars or lines will overflow the box and characters will
            # be silently lost past the canvas edge.
            x += nominal_advance * rng.uniform(0.84, 1.03)
    return canvas


def _safe_alpha_composite(canvas, glyph_img, paste_x, paste_y, box_w_px, box_h_px):
    """alpha_composite requires dest >= 0 and dest+size <= canvas size, so a
    glyph whose padded/rotated bounding box straddles the canvas edge (which
    happens routinely for characters near a box boundary) has to be cropped
    to the visible region first rather than pasted directly."""
    src_left = max(0, -paste_x)
    src_top = max(0, -paste_y)
    src_right = min(glyph_img.width, box_w_px - paste_x)
    src_bottom = min(glyph_img.height, box_h_px - paste_y)
    if src_right <= src_left or src_bottom <= src_top:
        return  # entirely off-canvas
    dest_x = max(0, paste_x)
    dest_y = max(0, paste_y)
    cropped = glyph_img.crop((src_left, src_top, src_right, src_bottom))
    canvas.alpha_composite(cropped, (dest_x, dest_y))


def _draw_checkmark_image(size_px, ink_color, rng):
    """Draw a hand-drawn checkmark (✓) as an RGBA image: a short down-stroke
    into a low vertex, then a longer up-stroke to the top-right. Every tick is
    deliberately different — the vertex position, arm lengths/angles, curvature,
    stroke width, ink darkness and a small overall rotation all vary — so no two
    checkmarks look identical (real ticks never are). Sized to fill `size_px`;
    callers usually place it slightly larger than the target box so it
    overshoots naturally."""
    # oversize the working canvas so a rotated tick isn't clipped
    W = H = int(size_px * 1.35)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    ox = (W - size_px) / 2
    oy = (H - size_px) / 2

    def jit(fx, fy, j=0.06):
        return (ox + fx * size_px + rng.uniform(-j, j) * size_px,
                oy + fy * size_px + rng.uniform(-j, j) * size_px)

    # randomize the tick geometry per instance
    vx = rng.uniform(0.34, 0.46)          # vertex x
    vy = rng.uniform(0.72, 0.86)          # vertex depth
    sx = rng.uniform(0.10, 0.24)          # short-arm start x
    sy = rng.uniform(0.44, 0.60)          # short-arm start height
    ex = rng.uniform(0.82, 0.96)          # long-arm end x
    ey = rng.uniform(0.06, 0.22)          # long-arm end height
    start = jit(sx, sy)
    vertex = jit(vx, vy)
    end = jit(ex, ey)
    curve = rng.uniform(0.0, 0.05)
    mid1 = ((start[0] + vertex[0]) / 2, (start[1] + vertex[1]) / 2 + H * curve)
    mid2 = ((vertex[0] + end[0]) / 2, (vertex[1] + end[1]) / 2 + H * curve)
    pts = [start, mid1, vertex, mid2, end]
    width = max(2, int(size_px * rng.uniform(0.07, 0.11)))
    alpha = rng.randint(232, 255)
    draw.line(pts, fill=ink_color + (alpha,), width=width, joint="curve")
    for (px, py) in (start, end):
        r = width / 2
        draw.ellipse([px - r, py - r, px + r, py + r], fill=ink_color + (alpha,))
    # small overall tilt so the whole tick leans a bit differently each time
    img = img.rotate(rng.uniform(-14, 10), resample=Image.BICUBIC, expand=False)
    return img


def overlay_handwritten_text(input_pdf, output_pdf, placements=None, font_path=DEFAULT_FONT_PATH,
                              ink_color=DEFAULT_INK_COLOR, dpi_scale=4, seed=None,
                              font_size=DEFAULT_FONT_SIZE_PT, min_size=MIN_FONT_SIZE_PT,
                              max_lines=DEFAULT_MAX_LINES, thinning=DEFAULT_STROKE_THINNING,
                              line_height_mult=1.6, checkmarks=None, checkmark_scale=1.3,
                              checkmark_dpi_scale=8):
    """Overlay simulated handwriting (and optional checkmarks) onto `input_pdf`
    and save to `output_pdf` (must differ from input_pdf; source untouched).

    The text is set at the fixed `font_size` (normal handwriting size — it
    does NOT scale up to fill tall boxes) and wraps to at most `max_lines`
    lines, stepping the size down toward `min_size` only when the text is too
    long to fit the line cap otherwise. `thinning` erodes stroke weight.

    placements: list of dicts, each:
        {"page": int (0-indexed), "text": str, "box": (x, y, w, h) in PDF points}
        optional per-placement overrides: "font_size", "ink_color", "font_path"
        (a per-placement "font_size" pins that exact size, skipping the
        max_lines step-down logic);
        optional "whiteout": (x0, y0, x1, y1) — an opaque white rectangle drawn
        BEFORE the handwriting, to cover pre-existing machine-typed text that
        the handwriting is replacing (keep it to the text line's bbox so it
        never reaches the cell borders).

    checkmarks: optional list of {"page": int, "box": (x0, y0, x1, y1)} — draws
        a hand-drawn ✓ centered on each box, scaled by `checkmark_scale` so it
        slightly overshoots (natural for a checkbox tick).
    """
    assert str(input_pdf) != str(output_pdf), "output_pdf must differ from input_pdf"
    rng = random.Random(seed)
    doc = fitz.open(input_pdf)
    try:
        for p in (placements or []):
            page = doc[p["page"]]
            x, y, w, h = p["box"]
            f_path = p.get("font_path", font_path)
            ink = p.get("ink_color", ink_color)
            if "whiteout" in p:
                wx0, wy0, wx1, wy1 = p["whiteout"]
                page.draw_rect(fitz.Rect(wx0, wy0, wx1, wy1), color=None,
                               fill=(1, 1, 1), fill_opacity=1, overlay=True)
            if "font_size" in p:
                size = p["font_size"]
                tmp = Image.new("RGBA", (10, 10))
                lines = _wrap_chars(p["text"], ImageFont.truetype(f_path, int(size * dpi_scale)),
                                     w * dpi_scale * _WRAP_WIDTH_FRACTION, ImageDraw.Draw(tmp))
            else:
                size, lines = fit_font_size_and_wrap(p["text"], w, h, f_path,
                                                      preferred_size=font_size,
                                                      min_size=min_size, max_lines=max_lines,
                                                      dpi_scale=dpi_scale)
            field_img = compose_field_image(lines, f_path, size, w, h, ink_color=ink,
                                             dpi_scale=dpi_scale, rng=rng, thinning=thinning,
                                             line_height_mult=line_height_mult)
            buf = io.BytesIO()
            field_img.save(buf, format="PNG")
            pix = fitz.Pixmap(buf.getvalue())
            page.insert_image(fitz.Rect(x, y, x + w, y + h), pixmap=pix, overlay=True)

        for c in (checkmarks or []):
            page = doc[c["page"]]
            bx0, by0, bx1, by1 = c["box"]
            bw, bh = bx1 - bx0, by1 - by0
            base = max(bw, bh)
            # per-tick size + position variation so no two checkmarks match,
            # and none sits perfectly centered (real ticks are placed by hand).
            sc = checkmark_scale * rng.uniform(0.88, 1.20)
            off_x = rng.uniform(-0.22, 0.22) * base
            off_y = rng.uniform(-0.20, 0.20) * base
            side_px = max(8, int(base * sc * checkmark_dpi_scale))
            ink = c.get("ink_color", ink_color)
            mark = _draw_checkmark_image(side_px, ink, rng)
            cx, cy = (bx0 + bx1) / 2 + off_x, (by0 + by1) / 2 + off_y
            half = base * sc / 2
            rect = fitz.Rect(cx - half, cy - half, cx + half, cy + half)
            buf = io.BytesIO()
            mark.save(buf, format="PNG")
            page.insert_image(rect, pixmap=fitz.Pixmap(buf.getvalue()), overlay=True)

        doc.save(output_pdf)
    finally:
        doc.close()


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_pdf")
    parser.add_argument("output_pdf")
    parser.add_argument("placements_json", help="JSON file: list of {page, text, box:[x,y,w,h]}")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    with open(args.placements_json, encoding="utf-8") as f:
        raw = json.load(f)
    placements = [{"page": p["page"], "text": p["text"], "box": tuple(p["box"])} for p in raw]
    overlay_handwritten_text(args.input_pdf, args.output_pdf, placements, seed=args.seed)
    print(f"wrote {args.output_pdf}")
