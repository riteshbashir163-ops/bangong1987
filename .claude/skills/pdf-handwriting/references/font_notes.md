# Font selection notes

**Chosen font: LXGW WenKai (霞鹜文楷)**, bundled at `assets/fonts/LXGWWenKai-Regular.ttf`
(from the `fonts-lxgw-wenkai` apt package, OFL-licensed). Use this as
`DEFAULT_FONT_PATH` unless you have a specific reason to try something else —
and if you do, repeat the verification procedure below before trusting it.

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
reading it. Zhi Mang Xing didn't have that specific bug, but as a very
cursive/grass-script style it produced a couple of other characters (e.g. 求,
规) that were hard to confidently read at a glance — too risky for a formal
document someone is legally attesting to.

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

## Why LXGW WenKai won

It's a professionally maintained, systematically-designed Kaiti-style font
(not digitized from a limited handwriting sample set), so its character
coverage and consistency across thousands of glyphs is far more reliable than
a font built from one person's scanned handwriting samples. It also reads as
neat, legible pen handwriting (natural stroke variation, clearly non-print)
rather than either sterile print or illegible cursive — a good fit for
official documents that must remain readable. All 57 characters needed for
this task's 3 review-opinion strings passed the visual verification above
with zero mismatches.
