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
from PIL import Image, ImageDraw, ImageFont

DEFAULT_FONT_PATH = str(Path(__file__).resolve().parent.parent / "assets" / "fonts" / "LXGWWenKai-Regular.ttf")
DEFAULT_INK_COLOR = (18, 18, 64)  # dark blue-black, matches Chinese pen-signature convention

_PUNCT_NO_LINE_START = set("，。、；：！？」』】)>》")


# Fraction of box width reserved as wrap budget. Must stay comfortably above
# 1.0 / (1 - max jitter headroom) used in _render_glyph's advance jitter so
# that per-character spacing jitter can never push a line past the box edge
# (see compose_field_image: advance jitter is uniform(0.96, 1.06)).
_WRAP_WIDTH_FRACTION = 0.86


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
                            min_size=14, max_size=26, dpi_scale=6, line_height_mult=1.65):
    """Pick the largest font size in [min_size, max_size] pt whose greedy
    character-wrap fits within box_h_pt at line_height_mult * size. Returns
    (font_size_pt, list_of_lines). Falls back to min_size (possibly
    overflowing) if nothing fits."""
    tmp = Image.new("RGBA", (10, 10))
    draw = ImageDraw.Draw(tmp)
    box_w_px = box_w_pt * dpi_scale
    box_h_px = box_h_pt * dpi_scale
    for size in range(max_size, min_size - 1, -1):
        font = ImageFont.truetype(font_path, int(size * dpi_scale))
        lines = _wrap_chars(text, font, box_w_px * _WRAP_WIDTH_FRACTION, draw)
        total_height = size * dpi_scale * line_height_mult * len(lines)
        if total_height <= box_h_px:
            return size, lines
    font = ImageFont.truetype(font_path, int(min_size * dpi_scale))
    lines = _wrap_chars(text, font, box_w_px * _WRAP_WIDTH_FRACTION, draw)
    return min_size, lines


def _render_glyph(ch, font, ink_color, rng):
    """Render a single character to its own padded transparent image with a
    small random rotation, simulating natural pen-stroke variation."""
    bbox = font.getbbox(ch)
    w = max(1, bbox[2] - bbox[0])
    h = max(1, bbox[3] - bbox[1])
    pad = int(max(w, h) * 0.4) + 4
    img = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    alpha = rng.randint(222, 255)
    draw.text((pad - bbox[0], pad - bbox[1]), ch, font=font, fill=ink_color + (alpha,))
    angle = rng.uniform(-4.0, 4.0)
    return img.rotate(angle, resample=Image.BICUBIC, expand=True)


def compose_field_image(lines, font_path, font_size_pt, box_w_pt, box_h_pt,
                         ink_color=DEFAULT_INK_COLOR, dpi_scale=6, rng=None,
                         line_height_mult=1.65):
    """Lay characters out along a slightly wavy per-line baseline with
    independent per-character jitter (rotation, vertical offset, scale,
    spacing). Returns an RGBA image sized box_w_pt*dpi_scale x
    box_h_pt*dpi_scale (transparent background) ready to overlay on a PDF box."""
    rng = rng or random.Random()
    box_w_px = int(box_w_pt * dpi_scale)
    box_h_px = int(box_h_pt * dpi_scale)
    canvas = Image.new("RGBA", (box_w_px, box_h_px), (0, 0, 0, 0))
    font_px = int(font_size_pt * dpi_scale)
    font = ImageFont.truetype(font_path, font_px)

    line_height = font_px * line_height_mult
    total_text_height = line_height * len(lines)
    top_margin = max(0.0, (box_h_px - total_text_height) / 2)
    left_margin = font_px * 0.12

    draw_measure = ImageDraw.Draw(canvas)
    for li, line in enumerate(lines):
        baseline_y = top_margin + li * line_height + line_height * 0.72
        x = left_margin
        wave_phase = rng.uniform(0, 2 * math.pi)
        for ch in line:
            nominal_advance = _char_advance(ch, font, draw_measure)
            if ch == " ":
                x += nominal_advance
                continue
            glyph_img = _render_glyph(ch, font, ink_color, rng)
            scale = rng.uniform(0.94, 1.08)
            if abs(scale - 1.0) > 1e-3:
                new_size = (max(1, int(glyph_img.width * scale)), max(1, int(glyph_img.height * scale)))
                glyph_img = glyph_img.resize(new_size, Image.LANCZOS)
            wave_offset = math.sin(x / (font_px * 2.2) + wave_phase) * font_px * 0.05
            jitter_y = rng.uniform(-0.04, 0.04) * font_px
            # center the (padded, possibly larger) glyph image on the cursor
            # column so ink can overflow its own cell slightly (natural for
            # handwriting) without perturbing the cursor's advance math.
            paste_x = int(x - (glyph_img.width - nominal_advance) / 2)
            paste_y = int(baseline_y - glyph_img.height * 0.72 + wave_offset + jitter_y)
            _safe_alpha_composite(canvas, glyph_img, paste_x, paste_y, box_w_px, box_h_px)
            # advance by the nominal (un-jittered) width, with only a small
            # cosmetic jitter — this MUST stay close to the width budget used
            # by _wrap_chars or lines will overflow the box and characters
            # will be silently lost past the canvas edge.
            x += nominal_advance * rng.uniform(0.96, 1.06)
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


def overlay_handwritten_text(input_pdf, output_pdf, placements, font_path=DEFAULT_FONT_PATH,
                              ink_color=DEFAULT_INK_COLOR, dpi_scale=4, seed=None,
                              min_size=14, max_size=26):
    """Overlay simulated handwriting onto `input_pdf` at each placement and
    save to `output_pdf` (must differ from input_pdf; source is never modified).

    placements: list of dicts, each:
        {"page": int (0-indexed), "text": str, "box": (x, y, w, h) in PDF points}
        optional per-placement overrides: "font_size", "ink_color", "font_path"
    """
    assert str(input_pdf) != str(output_pdf), "output_pdf must differ from input_pdf"
    rng = random.Random(seed)
    doc = fitz.open(input_pdf)
    try:
        for p in placements:
            page = doc[p["page"]]
            x, y, w, h = p["box"]
            f_path = p.get("font_path", font_path)
            ink = p.get("ink_color", ink_color)
            if "font_size" in p:
                size = p["font_size"]
                tmp = Image.new("RGBA", (10, 10))
                lines = _wrap_chars(p["text"], ImageFont.truetype(f_path, int(size * dpi_scale)),
                                     w * dpi_scale * _WRAP_WIDTH_FRACTION, ImageDraw.Draw(tmp))
            else:
                size, lines = fit_font_size_and_wrap(p["text"], w, h, f_path,
                                                      min_size=min_size, max_size=max_size,
                                                      dpi_scale=dpi_scale)
            field_img = compose_field_image(lines, f_path, size, w, h, ink_color=ink,
                                             dpi_scale=dpi_scale, rng=rng)
            buf = io.BytesIO()
            field_img.save(buf, format="PNG")
            pix = fitz.Pixmap(buf.getvalue())
            page.insert_image(fitz.Rect(x, y, x + w, y + h), pixmap=pix, overlay=True)
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
