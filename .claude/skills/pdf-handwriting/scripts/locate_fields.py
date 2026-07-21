"""Assistive (NOT authoritative) helpers for finding where a blank field lives
on a new PDF form, given a label like "审查意见" or "外观检查".

Chinese form layouts vary enormously between organizations, so this can only
ever suggest a candidate box — it has no way to know if a table cell extends
another 40pt to the right or if the real blank area is actually on the next
line. ALWAYS render the page to a PNG (see suggest_blank_cell's docstring)
and look at it before trusting a suggested box; treat this the same way the
coordinates for the original 报审表/验收记录 forms were derived: by reading
page.get_text("dict") output and reasoning about the layout directly when
the heuristic below isn't good enough.
"""

import fitz  # PyMuPDF


def find_checkbox_glyphs(page, codepoint=0xE5B6):
    """Return the bounding rects of every checkbox glyph on `page`, sorted
    top-to-bottom then left-to-right.

    Many Chinese official forms draw their checkboxes not as vector rectangles
    but as a private-use-area "hollow square" text glyph (e.g. U+E5B6 in an
    embedded subset font). Such glyphs do NOT appear as '□' in
    page.get_text() plain text, so they must be found by scanning the char
    spans of get_text("rawdict") for the exact codepoint.

    Returns a list of fitz.Rect. Grouping the result into fields (e.g. "the
    first N belong to the 监理 block, the next N to the 建设 block") and
    picking, say, the leftmost box on each line as the affirmative option is
    left to the caller, since it depends on the form — ALWAYS render the page
    and confirm which box is which before drawing a checkmark into it.
    """
    rects = []
    data = page.get_text("rawdict")
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for ch in span.get("chars", []):
                    if ord(ch["c"]) == codepoint:
                        rects.append(fitz.Rect(ch["bbox"]))
    rects.sort(key=lambda r: (round(r.y0, 1), round(r.x0, 1)))
    return rects


def find_label_candidates(pdf_path, page_index, label_text):
    """Return every bounding box (fitz.Rect, top-left origin) where
    `label_text` literally appears on the page. Try a few label variants
    (with/without trailing "：" or spaces) if the first search comes up
    empty — OCR'd or oddly-kerned labels sometimes don't match exactly."""
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_index]
        return page.search_for(label_text)
    finally:
        doc.close()


def suggest_blank_cell(pdf_path, page_index, label_rect, direction="below", max_extent_pt=200):
    """Best-effort guess at the blank cell that belongs to a label at
    `label_rect`. Walks the page's text blocks and vector drawings to find
    the next obstruction in `direction` ("below" or "right") and proposes a
    box bounded by that obstruction (or max_extent_pt, whichever is closer).

    This is intentionally conservative and will often be wrong for complex
    tables — it does not understand merged cells, multi-row spans, or
    labels split across lines. Always verify by rendering the page:

        import fitz
        doc = fitz.open(pdf_path)
        pix = doc[page_index].get_pixmap(dpi=150)
        pix.save("check.png")
        # then view check.png and compare against the suggested box, and
        # against the coordinates you can read directly from
        # page.get_text("dict")["blocks"] if the guess looks off.
    """
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_index]
        words = page.get_text("words")  # (x0, y0, x1, y1, text, block, line, word)
        drawings = page.get_drawings()

        if direction == "below":
            search_top = label_rect.y1
            search_bottom = search_top + max_extent_pt
            obstruction_y = search_bottom
            for w in words:
                wy0 = w[1]
                if search_top < wy0 < obstruction_y and not (w[0] < label_rect.x1 and w[2] > label_rect.x0):
                    obstruction_y = min(obstruction_y, wy0)
            for d in drawings:
                for item in d.get("items", []):
                    for pt_group in _extract_points(item):
                        for pt in pt_group:
                            if search_top < pt.y < obstruction_y:
                                obstruction_y = min(obstruction_y, pt.y)
            return fitz.Rect(label_rect.x0, search_top, page.rect.width - 40, obstruction_y)

        elif direction == "right":
            search_left = label_rect.x1
            search_right = search_left + max_extent_pt * 2
            obstruction_x = search_right
            for w in words:
                if abs(w[1] - label_rect.y0) < 5 and w[0] > search_left:
                    obstruction_x = min(obstruction_x, w[0])
            return fitz.Rect(search_left, label_rect.y0, obstruction_x, label_rect.y1 + 20)

        else:
            raise ValueError("direction must be 'below' or 'right'")
    finally:
        doc.close()


def _extract_points(item):
    """Pull point-like coordinates out of a get_drawings() item tuple,
    which varies in shape by drawing command (line/rect/curve/...)."""
    for el in item[1:]:
        if hasattr(el, "x") and hasattr(el, "y"):
            yield [el]
        elif isinstance(el, (list, tuple)):
            pts = [p for p in el if hasattr(p, "x")]
            if pts:
                yield pts
