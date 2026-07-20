---
name: pdf-handwriting
description: Fill blank review-opinion, comment, or remark fields on Chinese paperwork (e.g. 审查意见, 外观检查, 检查验收结果 boxes on 监理/工程验收 forms, inspection or acceptance records, and similar official documents) with text rendered as realistic simulated pen handwriting — not machine/print font — then return the completed PDF. Trigger whenever the user uploads a Chinese form PDF and asks to fill in review comments by hand, wants inserted text to look authentically handwritten/非打印体 so it's convincing when printed or signed off, or says things like "手写"/"模拟手写"/"用手写体填写". Not for generic PDF merge/split/OCR/AcroForm field-filling (use the pdf skill instead), and not for Word/Excel/PowerPoint documents (use docx/xlsx/pptx).
---

# PDF Handwriting Fill

Overlays Chinese text rendered as realistic simulated pen handwriting onto
specific blank areas of a PDF — for filling in review-opinion / inspection
fields on Chinese construction-supervision-style forms so the printed result
looks hand-filled rather than typed.

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
   fill, NOT including the printed label. The function auto-picks a font size
   (14–26pt) and wraps per-character to fit, applies per-character rotation /
   scale / baseline-wave jitter for a natural handwritten look, and inserts
   the result as an image at that exact rect via PyMuPDF, leaving all other
   page content untouched. Default ink color is dark blue-black
   `(18,18,64)` — matches Chinese pen-signature convention; avoid red.

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
   - Every character reads correctly (see the font-bug story below — don't
     just eyeball "looks handwritten," actually read the sentence).
   - Visibly non-mechanical (jitter/wave present) but still legible.
   - Ink color is black/blue-black, not red or washed out.
   - Everything else on the page is visually unchanged from the source.
   If anything's off, adjust and re-render before delivering — don't ship on
   the first attempt without looking.

4. **Deliver.** Send the finished PDF with `SendUserFile`. Never leave the
   only copy sitting in a temp dir without sending it.

## Font: read this before changing it

`assets/fonts/LXGWWenKai-Regular.ttf` (LXGW WenKai / 霞鹜文楷) is the
default and has been verified character-by-character against the specific
text used in this skill's original task. **Do not swap in a different
handwriting font (e.g. a Google Fonts cursive/brush font) without repeating
the full visual verification procedure in `references/font_notes.md` first**
— one of the obvious-looking candidates (Ma Shan Zheng) turned out to render
料 (material) visually identical to 科, silently corrupting the text's
meaning, and neither cmap-coverage checks nor several pixel-similarity
heuristics caught it. Only reading the actual rendered characters did. If a
new document needs characters outside what's already been verified, at
minimum run the cmap-coverage check from `font_notes.md`, and ideally the
full reference-grid visual check for any character you haven't already
confirmed.

## Files

- `scripts/render_handwriting.py` — core rendering + overlay engine
  (`overlay_handwritten_text`, `fit_font_size_and_wrap`, `compose_field_image`).
- `scripts/locate_fields.py` — assistive field-location heuristics
  (`find_label_candidates`, `suggest_blank_cell`) — always eyeball the result.
- `scripts/requirements.txt` — `pip3 install --user -r requirements.txt`
  (pymupdf, pillow, fonttools) if those aren't already available.
- `assets/fonts/LXGWWenKai-Regular.ttf` — the verified default font.
- `references/font_notes.md` — why this font was chosen, why two other
  candidates were rejected, and the verification procedure to reuse before
  trusting any new font.
