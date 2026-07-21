# Font selection notes

## Round-3 user feedback: thinner strokes + replace machine text + checkmarks

- **Thinner strokes.** The round-1/2 output (Zhi Mang Xing at full weight)
  read as too heavy. `DEFAULT_STROKE_THINNING = 1.0` now erodes each glyph's
  alpha (`_thin_alpha` via `MinFilter`) so strokes are finer than the font's
  native weight, and the old random `stroke_width` pen-pressure *thickening*
  pass was removed. Tune the value per document by looking at the render
  (higher = thinner; too high breaks thin strokes). Keep it thin — the user
  explicitly wanted a fine-pen look, not the heavier earlier weight.
- **Replacing machine-typed text** (not just filling blanks) became a needed
  capability: `overlay_handwritten_text` placements accept a `whiteout` rect
  that paints opaque white over the old print before the handwriting goes on.
  Used to convert a whole machine-filled form (施工/监理/建设 opinions +
  质量等级 合格 cells) to handwriting.
- **Hand-drawn checkmarks** in checkboxes: `checkmarks=[{page, box}]` +
  `_draw_checkmark_image`. On the 分部工程质量评定表 the checkboxes were
  private-use text glyphs (U+E5B6), found via `find_checkbox_glyphs`.
- **Small-cell sizing:** that form's cells hold ~8pt machine text, so the 19pt
  default was wrong there — a per-placement `font_size ≈ 10` was used. Size to
  the document's cells; the "fixed size" rule is per-document, not a global 19.

## Round-2 user feedback: ink color, size, line count (do not revert)

After the round-1 font swap (below) shipped, the user gave three more
concrete corrections, all now baked into `render_handwriting.py` defaults:

1. **Ink must be black**, not the blue-black `(18,18,64)` this skill started
   with — `DEFAULT_INK_COLOR = (0, 0, 0)`.
2. **Font size must NOT scale up to fill a bigger box.** The original
   `fit_font_size_and_wrap` grew the size until the text filled the box
   height, which made the same sentence look conspicuously bigger in a tall
   box (page 1's `审查意见`, ~142pt tall) than in a short one (page 2's
   fields, ~30–54pt tall) — obviously wrong, since a real person writes at
   the same size regardless of how much blank space is available.
   `DEFAULT_FONT_SIZE_PT = 19` (the size the user had already approved on
   page 2) is now fixed and does not respond to box height at all.
3. **Cap at 2 lines** — `DEFAULT_MAX_LINES = 2`. `fit_font_size_and_wrap` only
   steps the font size down (toward `MIN_FONT_SIZE_PT = 14`) if the text
   can't be wrapped into 2 lines at 19pt; it never grows past 19pt even when
   a box is tall enough for the text to comfortably sit on one line.

A side effect: `compose_field_image` was changed from vertically centering
text in the box to **top-aligning** it (small top margin only), since a
fixed size no longer reliably fills tall boxes and centering left an odd gap
above short text — top alignment matches where a real reviewer's pen would
actually land, right under the printed label.

**Chosen font: Zhi Mang Xing (志莽行书)**, bundled at
`assets/fonts/ZhiMangXing-Regular.ttf` (Google Fonts, OFL-licensed). This is
`DEFAULT_FONT_PATH` in `render_handwriting.py`. **This was an explicit user
correction** — the first version of this skill shipped with LXGW WenKai
(still bundled as `LEGIBLE_FALLBACK_FONT_PATH`) because it tested as
completely bug-free and highly legible, but the user rejected that output as
"not looking handwritten at all" and provided reference photos of genuine
cursive Chinese handwriting: connected/flowing strokes between characters,
a consistent rightward italic slant, and visible pen-pressure variation
(thicker strokes where the pen pressed harder, thinner trailing strokes).
LXGW WenKai's neat, evenly-weighted Kaiti letterforms could not produce that
look no matter how the compositing jitter was tuned — the fix had to be a
different, genuinely cursive font plus rendering changes (see
`render_handwriting.py`: `_render_glyph`'s shear/stroke-width jitter and
`compose_field_image`'s tighter/overlapping character spacing), not just
more randomization on top of a print-shaped font.

**Takeaway for future font choices: prioritize matching the "real handwriting"
look (slant, connected flow, pressure variation) over legibility-at-a-glance.**
The user's own reference samples were themselves hard to read at a glance —
that's expected and correct for authentic cursive script, not a defect to
fix. Don't second-guess this back toward a neater font without the user
asking for it again.

## Why not the obvious Google Fonts handwriting fonts

Two open-license cursive/brush fonts are trivially downloadable from
`fonts.gstatic.com` (works even though `fonts.google.com`/`github.com` are
blocked by this environment's proxy — go through
`https://fonts.googleapis.com/css2?family=<Name>` to get the real `.ttf` URL):

- Ma Shan Zheng 马善政毛笔 — `https://fonts.gstatic.com/s/mashanzheng/v17/NaPecZTRCLxvwo41b4gvzkXaRMQ.ttf`
- Zhi Mang Xing 志莽行书 — `https://fonts.gstatic.com/s/zhimangxing/v19/f0Xw0ey79sErYFtWQ9a2rq-g0ac.ttf`

Both looked plausible at first glance (full cmap coverage, no `ModuleNotFoundError`,
render without crashing). **Ma Shan Zheng was disqualified after actual visual
verification**: its glyph for 料 (U+6599, "material") renders visually
identical to 科 (U+79D1) — a real digitization/design bug in the font, not a
rendering bug in this skill's code. "施工材料" would silently read as the
nonsensical "施工材科" to anyone reading the printed page. This was **not**
caught by cmap-coverage checks, glyph-outline-coordinate diffing, or several
flavors of pixel-similarity hashing — those all say the two glyphs are
"different enough." It was only caught by rendering the specific text and
reading it. **Zhi Mang Xing passed the same character-by-character
verification with no such bug** (see procedure below) and is the font
actually in use — its more cursive/grass-script letterforms make a couple of
characters (e.g. 求, 规) harder to read in isolation than a print font would
be, but that's an accurate property of real cursive handwriting, not a
correctness defect, and matches what the user explicitly asked for.

**Lesson: automated glyph-coverage/pixel-diff checks are not sufficient to
trust a handwriting font for real document content.** They catch missing
glyphs and duplicate glyph IDs, not "font ships a wrong or misleadingly
similar glyph shape for a real character." The only reliable check is
rendering every character you actually plan to use and reading it.

## Verification procedure (repeat this if you swap fonts)

1. Confirm cmap coverage for every unique character in the target text (quick
   filter, not sufficient alone):
   ```python
   from fontTools.ttLib import TTFont
   cmap = TTFont(font_path).getBestCmap()
   missing = [ch for ch in required_chars if ord(ch) not in cmap]
   ```
2. Build a reference-vs-candidate grid: render each required character small
   in a known-correct print font (e.g. `/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc`,
   present on this system) directly above the same character rendered large in
   the candidate handwriting font. See
   `references/build_verify_grid_example.py`-style code in git history /
   the implementation session — the core idea:
   ```python
   draw.text((x, y), ch, font=ref_font, fill=(150,150,150))   # small, gray
   draw.text((x, y+50), ch, font=hw_font, fill=(0,0,0))       # large, black
   ```
3. Actually look at the rendered grid image (Read tool) and read every cell,
   character by character, comparing the handwriting glyph's radicals/strokes
   against the reference. Zoom into (crop + resize) any cell that looks
   ambiguous before accepting it.
4. Only after every character passes visual review, copy the font into
   `assets/fonts/` and update `DEFAULT_FONT_PATH` in `render_handwriting.py`.

## LXGW WenKai — kept as `LEGIBLE_FALLBACK_FONT_PATH`, not the default

It's a professionally maintained, systematically-designed Kaiti-style font
(not digitized from a limited handwriting sample set), so its character
coverage and consistency across thousands of glyphs is unusually reliable,
and all 57 characters needed for this task passed visual verification with
zero mismatches. It's kept in `assets/fonts/` as a fallback for situations
where legibility must trump handwritten authenticity (e.g. if a user
explicitly asks for something easier to read, or a new document's text turns
out to have characters that don't verify cleanly in Zhi Mang Xing). But it
reads as neat print-adjacent Kaiti, not genuine cursive handwriting — do not
use it as the default without a specific reason, per the user correction
recorded at the top of this file.
