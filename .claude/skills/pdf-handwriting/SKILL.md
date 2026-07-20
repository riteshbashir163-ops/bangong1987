---
name: pdf-handwriting
description: Fill blank review-opinion, comment, or remark fields on Chinese paperwork (e.g. 审查意见, 外观检查, 检查验收结果 boxes on 监理/工程验收 forms, inspection or acceptance records, and similar official documents) with text rendered as realistic simulated pen handwriting — not machine/print font — then return the completed PDF. Trigger whenever the user uploads a Chinese form PDF and asks to fill in review comments by hand, wants inserted text to look authentically handwritten/非打印体 so it's convincing when printed or signed off, or says things like "手写"/"模拟手写"/"用手写体填写". Not for generic PDF merge/split/OCR/AcroForm field-filling (use the pdf skill instead), and not for Word/Excel/PowerPoint documents (use docx/xlsx/pptx).
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
- **Max 2 lines** — `DEFAULT_MAX_LINES = 2`. Font size only steps down (never
  below `MIN_FONT_SIZE_PT`) if the text genuinely can't fit 2 lines at 19pt;
  it never grows past 19pt even if 1 line would fit easily in a short box.
- Text is **top-aligned**, not vertically centered, so it starts right under
  the label like real handwriting would, rather than floating mid-box.

See `references/font_notes.md` for the full story on both rounds.

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
   - Ink color is black/blue-black, not red or washed out.
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
  (`overlay_handwritten_text`, `fit_font_size_and_wrap`, `compose_field_image`).
- `scripts/locate_fields.py` — assistive field-location heuristics
  (`find_label_candidates`, `suggest_blank_cell`) — always eyeball the result.
- `scripts/requirements.txt` — `pip3 install --user -r requirements.txt`
  (pymupdf, pillow, fonttools) if those aren't already available.
- `assets/fonts/ZhiMangXing-Regular.ttf` — the verified default (cursive) font.
- `assets/fonts/LXGWWenKai-Regular.ttf` — verified legible fallback font, not
  the default (see "Target look" above for why).
- `references/font_notes.md` — why the default font is what it is, including
  the user correction that changed it, why other candidates were rejected,
  and the verification procedure to reuse before trusting any new font.
