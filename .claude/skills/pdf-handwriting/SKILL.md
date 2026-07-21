---
name: pdf-handwriting
description: Fill or replace fields on Chinese paperwork (e.g. 审查意见, 外观检查, 检查验收结果, 施工/监理/建设单位意见 boxes on 监理/工程验收/质量评定 forms and similar official documents) with text rendered as realistic simulated pen handwriting — not machine/print font — and optionally hand-draw ✓ checkmarks in checkboxes, then return the completed PDF. Handles both blank fields AND replacing existing machine-typed text (whites it out, then handwrites over it). Trigger whenever the user uploads a Chinese form PDF and asks to fill in / replace comments by hand, tick 打钩 checkboxes by hand, wants inserted text to look authentically handwritten/非打印体 so it's convincing when printed or signed off, or says things like "手写"/"模拟手写"/"用手写体填写"/"替换成手写"/"手写打钩". Not for generic PDF merge/split/OCR/AcroForm field-filling (use the pdf skill instead), and not for Word/Excel/PowerPoint documents (use docx/xlsx/pptx).
---

# PDF Handwriting Fill

Overlays Chinese text rendered as realistic simulated pen handwriting onto
specific blank areas of a PDF — for filling in review-opinion / inspection
fields on Chinese construction-supervision-style forms so the printed result
looks hand-filled rather than typed.

**Target look, per explicit user corrections (two rounds so far):** genuine
cursive/running-hand (行书) handwriting — visible rightward slant, strokes
that read as connected and flowing between characters, and pen-pressure
variation (some strokes heavier, some lighter/trailing) — not neat,
evenly-weighted Kaiti print style. An earlier version of this skill defaulted
to a clean, highly legible Kaiti font and the user rejected it as "not
looking handwritten at all," providing photos of real handwritten notes as
the reference. Authentic cursive is allowed — even expected — to be a little
harder to read at a glance than print; don't over-optimize for legibility at
the cost of that look.

A second round of feedback fixed three more defaults that must NOT be
reverted without the user asking again:
- **Black ink**, not blue-black — `DEFAULT_INK_COLOR = (0, 0, 0)`.
- **Fixed font size**, not scaled to fill the box — `DEFAULT_FONT_SIZE_PT = 19`,
  matching normal handwriting size. A big box just gets more empty space
  below the text, not bigger characters — that's what a real person does.
  (Size is fixed *within one document*; different documents use whatever
  fixed size fits their cells — see the small-cell note under "Sizing".)
- **Max 2 lines** — `DEFAULT_MAX_LINES = 2`. Font size only steps down (never
  below `MIN_FONT_SIZE_PT`) if the text genuinely can't fit 2 lines at 19pt;
  it never grows past 19pt even if 1 line would fit easily in a short box.
- Text is **top-aligned**, not vertically centered, so it starts right under
  the label like real handwriting would, rather than floating mid-box.

A third round asked for **thinner strokes**, then course-corrected — thin but
still clearly visible, and **at real printed handwriting size**:
- `DEFAULT_STROKE_THINNING = 0.3` (mild erosion via `_thin_alpha`) — thinning
  `1.0` made it faint/"不明显"; keep the ink solid black and prominent (alpha
  floor ~238–255). "Thinner" = a finer pen line, not a faint one.
- Size to what a person actually writes on paper (~14–18pt), **not** the tiny
  machine-print size being replaced. Match real handwriting, subject to the
  cell fitting (long lines may wrap or auto-shrink to avoid clipping).
- **Lossless, high-res output:** render overlays at `dpi_scale=8` (~576 DPI at
  print size) and save with lossless zlib only —
  `doc.save(out, garbage=4, deflate=True, clean=True)`. Never JPEG/lossy.

See `references/font_notes.md` for the full story on all three rounds.

## Workflow

1. **Locate the target fields.** For a brand-new document you haven't seen
   before, use `scripts/locate_fields.py`'s `find_label_candidates()` /
   `suggest_blank_cell()` as a starting guess, but these are explicitly
   assistive, not authoritative — table layouts vary too much. Confirm (or
   just directly determine, skipping the heuristic) the real blank-box
   coordinates by reading `page.get_text("dict")` output yourself: find the
   label's bounding box, then reason about where the adjacent/below blank
   cell actually ends (next label, next drawn rule line, or table border).
   Coordinates are in PDF points, top-left origin, y increasing downward
   (matches `fitz`'s native coordinate space as long as the page has no
   rotation — sanity-check with `page.rect` / `page.rotation` first).

2. **Render and overlay.** Call `scripts/render_handwriting.py`'s
   `overlay_handwritten_text()`:

   ```python
   import sys
   sys.path.insert(0, "/home/user/bangong1987/.claude/skills/pdf-handwriting/scripts")
   from render_handwriting import overlay_handwritten_text

   overlay_handwritten_text(
       input_pdf="uploaded.pdf",
       output_pdf="/tmp/.../filled.pdf",   # NEVER the same path as input
       placements=[
           {"page": 0, "text": "经审查，本次进场的施工材料，其检测试验报告结果符合设计文件和相关技术规范要求，同意进场并使用于拟定部位。",
            "box": (76.56, 560.40, 481.80, 142.08)},
           {"page": 1, "text": "经检查，该批次材料无严重缺陷，整体合格。",
            "box": (76.56, 551.88, 481.80, 53.64)},
           {"page": 1, "text": "检查验收合格，同意进场。",
            "box": (76.56, 605.40, 481.80, 31.92)},
       ],
       seed=None,  # set an int for reproducible jitter while debugging
   )
   ```

   `page` is 0-indexed. `box` is `(x, y, w, h)` in points — the blank area to
   fill, NOT including the printed label. The function sets text at a FIXED
   normal-handwriting size (19pt by default — does not grow to fill a tall
   box), wraps per-character up to a 2-line cap (stepping the size down only
   if 2 lines genuinely won't hold the text), top-aligns it under the label,
   and applies per-character rotation / italic shear / stroke-weight
   (pen-pressure) / baseline-wave jitter with tight, slightly-overlapping
   spacing so characters read as a connected running hand rather than
   isolated stamps. The result is inserted as an image at that exact rect via
   PyMuPDF, leaving all other page content untouched. Default ink color is
   **black** (gel/rollerball pen) — do not use blue-black or red.

   Can also be run standalone from the CLI:
   ```
   python3 scripts/render_handwriting.py input.pdf output.pdf placements.json [--seed N]
   ```
   where `placements.json` is a list of `{"page": int, "text": str, "box": [x,y,w,h]}`.

   **Replacing existing machine-typed text** (not just filling a blank): add a
   `"whiteout": (x0, y0, x1, y1)` to the placement — an opaque white rectangle
   is drawn over that rect *before* the handwriting, covering the old print.
   Keep the whiteout to the machine text line's own bbox (pad ~1.5–2pt) so it
   never reaches cell borders or the line above/below. Get the machine text's
   bbox with `page.search_for("<the exact string>")` (it may return several
   rects for one visual line — union them). Then place the handwriting box on
   the same line (usually a bit wider, since handwriting runs wider than 8pt
   print). Example per placement:
   ```python
   {"page": p, "text": "同意监理意见。", "font_size": 10,
    "whiteout": (x0-1.5, y0-2, x1+1.5, y1+2),      # covers the old print
    "box": (cell_left, (y0+y1)/2 - h/2, cell_w, h)} # handwriting, same line
   ```

   **Checkmarks** (打钩 in a checkbox): pass `checkmarks=[{"page": p, "box":
   (x0,y0,x1,y1)}, ...]`; a hand-drawn ✓ is centered on each box (scaled to
   slightly overshoot, like a real tick). For checkboxes that are private-use
   text glyphs rather than drawn rectangles (common on Chinese forms, e.g.
   U+E5B6 hollow squares), locate them with
   `locate_fields.find_checkbox_glyphs(page)` — see that function's docstring
   for grouping which box is which (always render and confirm before ticking).

   **Sizing — write at real printed handwriting size (~14–18pt), not the size
   of the machine text you're replacing.** The 19pt default suits large
   review-comment boxes; dense forms like 分部工程质量评定表 (machine text ~8pt)
   still get ~14–16pt handwriting — a person writing on that form writes bigger
   than the print, wrapping a long opinion across 2 lines rather than writing
   tiny. Set per-placement `"font_size"`, allow `max_lines=2` for long lines,
   and for a width-constrained single-line slot pick the largest size that
   fits one line (measure with `ImageFont.getlength`) so nothing wraps off and
   gets clipped. Do NOT shrink to match the machine text — that reads as too
   small. Render at `dpi_scale=8` (~576 DPI) for crisp print and save
   **losslessly**: `doc.save(out, garbage=4, deflate=True, clean=True)` (zlib
   only — never lossy/JPEG; the user requires lossless output).

3. **Verify visually — mandatory, not optional.** Render the affected pages
   back to PNG and actually look at them:
   ```python
   import fitz
   doc = fitz.open(output_pdf)
   for i in range(doc.page_count):
       doc[i].get_pixmap(dpi=150).save(f"check_page{i+1}.png")
   ```
   Then use the Read tool on each PNG and check:
   - Handwriting sits fully inside its intended box — no overlap with labels,
     table borders, stamp boxes, signature/date lines, or other fields.
   - No overflow past the box or off the page.
   - Every character reads correctly when you read it carefully character by
     character (see the font-bug story below — a quick "looks handwritten"
     glance is not enough, the text must actually say what it's supposed to).
     A cursive style being harder to skim than print is fine and expected;
     a character silently having become a *different* character is not.
   - Visibly cursive/connected with slant and pressure variation, not a row
     of isolated stamped glyphs.
   - Ink color is black, not washed out. Strokes thin (per the thinner-strokes
     default) but not broken/faint.
   - When **replacing** machine text: the old print is fully covered (no grey
     ghosting behind or beside the handwriting) and the white patch didn't eat
     a cell border or an adjacent line.
   - When **ticking checkboxes**: the ✓ is in the intended box only (e.g. the
     affirmative 相符/同意, NOT 不相符/不同意), one per box, reading as a
     hand-drawn tick.
   - Everything else on the page is visually unchanged from the source.
   If anything's off, adjust and re-render before delivering — don't ship on
   the first attempt without looking.

4. **Deliver.** Send the finished PDF with `SendUserFile`. Never leave the
   only copy sitting in a temp dir without sending it.

## Font: read this before changing it

`assets/fonts/ZhiMangXing-Regular.ttf` (Zhi Mang Xing / 志莽行书, genuine
running-cursive style) is `DEFAULT_FONT_PATH` and has been verified
character-by-character against the specific text used in this skill's
original task. `assets/fonts/LXGWWenKai-Regular.ttf` is also bundled as
`LEGIBLE_FALLBACK_FONT_PATH` for the rare case legibility must win over
authenticity — do not switch to it as the default just because a cursive
render looks hard to read; that's expected, see the "Target look" note above.

**Do not swap in a different handwriting font without repeating the full
visual verification procedure in `references/font_notes.md` first** — one of
the obvious-looking candidates (Ma Shan Zheng) turned out to render 料
(material) visually identical to 科, silently corrupting the text's meaning,
and neither cmap-coverage checks nor several pixel-similarity heuristics
caught it. Only reading the actual rendered characters did. If a new document
needs characters outside what's already been verified, at minimum run the
cmap-coverage check from `font_notes.md`, and ideally the full reference-grid
visual check for any character you haven't already confirmed.

## Files

- `scripts/render_handwriting.py` — core rendering + overlay engine
  (`overlay_handwritten_text` with `placements` incl. optional `whiteout`,
  and `checkmarks`; plus `fit_font_size_and_wrap`, `compose_field_image`,
  `_draw_checkmark_image`, and the `thinning`/`DEFAULT_STROKE_THINNING` knob).
- `scripts/locate_fields.py` — assistive field-location heuristics
  (`find_label_candidates`, `suggest_blank_cell`, and `find_checkbox_glyphs`
  for private-use-area checkbox glyphs) — always eyeball the result.
- `scripts/requirements.txt` — `pip3 install --user -r requirements.txt`
  (pymupdf, pillow, fonttools) if those aren't already available.
- `assets/fonts/ZhiMangXing-Regular.ttf` — the verified default (cursive) font.
- `assets/fonts/LXGWWenKai-Regular.ttf` — verified legible fallback font, not
  the default (see "Target look" above for why).
- `references/font_notes.md` — why the default font is what it is, including
  the user correction that changed it, why other candidates were rejected,
  and the verification procedure to reuse before trusting any new font.
