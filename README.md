# prepress-preflight

A read-only command-line preflight checker for print PDFs. Give it a PDF and it
prints a report. It never writes to the file it is given.

This is the engine for a larger pre-press tool. Fixes, an interface and action
lists come later and will sit on top of it.

## Two rules the project is built on

1. **No change without confirmation.** This tool only inspects.
2. **Nothing altered beyond what was requested.** Checks cannot modify a file;
   the test suite verifies the input is byte-identical after a run. One check
   rearranges a page in memory to render parts of it separately; that copy is
   never saved.

## Install

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Use

```sh
preflight artwork.pdf                 # readable report
preflight artwork.pdf --trim 90x55    # also compare trim size with the job spec (mm)
preflight booklet.pdf --bound         # bound job: spine edges need no bleed
preflight artwork.pdf --ink-limit 300 # total ink limit in percent (default 400)
preflight artwork.pdf --safe-margin 3 # keep text and loose artwork 3 mm inside the trim (default 1.5)
preflight artwork.pdf --cmyk-only     # the job prints in CMYK only: any printing spot fails
preflight artwork.pdf --json          # same findings as data
preflight *.pdf                       # several files
```

Exit code: `0` no fails, `1` at least one fail, `2` a file could not be read.

Files of 8 MB or more run their slowest checks in parallel processes (the 56-page
catalogue sample takes 5 s instead of 18 s); the report is identical. Set
`PREFLIGHT_WORKERS=1` to keep everything in one process.

## Reports

```sh
preflight report artwork.pdf                       # writes "artwork preflight report.html" beside it
preflight report artwork.pdf --out ~/Desktop/r.html --ink-limit 320
preflight report jobs/ --out-dir ~/Desktop/today       # every PDF in a folder, plus a summary (index.html)
```

One HTML file to keep or send to a client: status, each finding with why it
matters, and pictures of the pages with the problems marked and numbered. The
PDF is only read; an existing file is never overwritten. In the app: Save report….

## The app

```sh
pip install -e ".[app]"       # optional: the app's own window, with drag and drop
preflight app                 # opens the app (its own window, or your browser without the extra)
preflight app artwork.pdf     # with a file open
preflight app --browser       # use the browser even when the window is available
```

Inspect a file (pages, the findings marked on them, trim and bleed, each
printing plate on its own with overprint honoured, and every object under a
point you click, topmost first; select one for its colour definition,
graphics state and the instructions that paint it), preview
a fix as before and after, keep the fixes you want in a review list, and
export them to a new file after confirming. It runs on this computer only,
reads files where they are, and never changes the original.

## Fixes

Fixes never change the file you give them. Join several with `+` to run them
in order on one preview and one confirmation. They show what they would change,
and write a new file only when asked and confirmed.

```sh
preflight fix trim-from-marks banner.pdf                      # show the proposal and its effect
preflight fix trim-from-marks banner.pdf --write banner-fixed.pdf   # write it, after you type yes
preflight fix set-boxes flyer.pdf --trim 210x297                   # set the trim to A4 and a 3 mm bleed box
preflight fix add-bleed canva.pdf --write canva-bleed.pdf           # make 3 mm of bleed where the artwork stops at the trim
preflight fix add-bleed canva.pdf --clip-to-bleed --marks --write canva-press.pdf   # also clip to the bleed box and add trim marks
preflight fix add-marks poster.pdf --write poster-marks.pdf          # trim marks only
preflight fix scale flyer.pdf --trim 148x210 --write flyer-a5.pdf    # scale A4 artwork to A5
preflight fix trim-from-marks+add-marks banner.pdf --clip-to-bleed --write press.pdf   # several fixes in one go
preflight actions save canva-press "add-bleed --clip-to-bleed --marks"   # save a chain under a name
preflight fix @canva-press canva.pdf --write canva-press.pdf          # and run it
preflight fix @canva-press jobs/ --out-dir jobs-fixed/                  # on every PDF in a folder
preflight watch ~/Hot --out ~/Hot-results --fix @canva-press --job coated   # a hot folder: report and fix what is dropped in
preflight jobs save coated -- --ink-limit 320 --safe-margin 3      # job settings under a name
preflight flyer.pdf --job coated                                     # use them (also report, app, fix)
preflight fix reduce-ink posters.pdf --job coated --write posters-320.pdf   # press limit from the job
preflight fix rename-spot job.pdf --spot "Pantone 185C=PANTONE 185 C" --write job-spots.pdf
preflight fix spot-to-cmyk catalogue.pdf --write catalogue-cmyk.pdf  # spots to CMYK
preflight fix rgb-to-cmyk yearbook.pdf --write yearbook-cmyk.pdf    # RGB to CMYK (FOGRA39)
preflight fix knock-out-white catalogue.pdf --write catalogue-ko.pdf   # all white overprint
preflight fix object-overprint catalogue.pdf --object 48:1240 --overprint off --write fixed.pdf  # one object
```

| Fix | What it changes |
| --- | --- |
| trim-from-marks | Sets the trim box to where the crop marks point, and the bleed box to that plus 3 mm (`--min-bleed`). Nothing else. |
| add-bleed | Makes bleed where artwork stops at the trim: the edge is mirrored outward (`--method stretch` repeats it instead) as an image beneath the artwork, outside the trim, and the page grows to hold it. `--clip-to-bleed` clips everything to the bleed box; `--marks` adds trim marks outside it, replacing any existing ones. The artwork inside the trim is proved unchanged. |
| add-marks | Adds trim marks outside the bleed box, without making bleed. Keeps existing marks that already point at the trim, unless `--clip-to-bleed` is also asked for. |
| scale | Scales the whole page so the trim is the job size (`--trim WxH`). Up to 1% change of shape is made exactly; beyond that it needs `--fill` (crop to cover) or `--fit` (fit inside). Crop marks are redrawn when the shape changes. |
| rename-spot | Renames a spot colour, or merges it into one already in the file (`--spot "OLD=NEW"`). Only the colour definitions change. |
| spot-to-cmyk | Converts spot colours to CMYK using each spot's own CMYK definition (`--spot NAME` for particular ones). A spot is converted only if every use of it can be; the result is verified to look the same. |
| registration-to-black | Makes registration colour inside the trim black at the same tint (Registration 100% becomes K100). Marks outside the trim stay registration. Uses the same trim as the check, inferred from crop marks when they disagree with the trim box. |
| rgb-to-cmyk | Converts RGB to CMYK through a profile: Coated FOGRA39 by default (`--profile gracol2006`, `swop` or a path to an .icc). Relative colorimetric with black point compensation. Pure RGB black text and vectors become K100 only (`--near-black-to-k` for near-black too). Photos stay JPEG at their own quality. Anything it cannot convert exactly is listed and left. |
| set-output-intent | Says which print condition the CMYK was made for (Coated FOGRA39 by default, `--profile` as for rgb-to-cmyk), with the profile embedded. Leaves an existing output intent alone unless `--replace`. Chain it after a conversion: `rgb-to-cmyk+set-output-intent`. |
| thicken-hairlines | Thickens open lines thinner than 0.25 pt to 0.25 pt as printed, each at its own scale. Outlines of closed shapes are left and listed (in illustrations they are often hair outlines meant to vanish). Check the preview on illustrated work. |
| reduce-ink | Brings colours over the press limit (`--ink-limit`, required, e.g. 300) down to it: grey moves into black first, then C, M and Y come down together. Flat colours and CMYK images (only the pixels over the limit; indexed images by palette). Colours within the limit are untouched. Gradients and spot or DeviceN images are listed and left. |
| rich-black-text | Makes small text in rich black (K 85% or more with C, M or Y) K100 only, up to 8 pt as placed (`--max-text`), as the Small text inks check warns. Other colours and overprint are left; chain overprint-black after it to overprint the new black. |
| overprint-black | Sets small 100% black (C0 M0 Y0 K100) text and thin black lines to overprint, with overprint mode 1, so misregistration cannot show a halo around them. Small means text up to 12 pt and lines up to 2 pt as placed (`--max-text`, `--max-line`); larger black keeps knocking out so a photo beneath does not show through. Rich black is not touched; black in DeviceGray is listed and left. Run after rgb-to-cmyk for RGB files. The Black overprint check (a note) lists the same objects. |
| knock-out-white | Knocks out every white object set to overprint (white overprint does not print), as the White overprint check reports them. Only those objects change, verified against every object in the file. Any it cannot change on its own are listed and left as fails. |
| recolour-object | Gives one object a new CMYK colour (`--object PAGE:NUMBER --cmyk C,M,Y,K` in percent). Easiest from the app: click the page, choose the object, Recolour…. Only the chosen side (fill or stroke) changes; overprint stays as it is. Shapes, text and stencil masks only; images and gradients carry their own colours. Verified against every other object on the page. |
| remove-private-data | Removes other applications' private data (page-piece data): the editing copy Illustrator keeps inside its PDFs, and InDesign's marker. Nothing in it prints. Afterwards Illustrator opens the file as plain PDF objects, not the original artwork. Use `--compact` for the file to get smaller. |
| downsample-images | Brings images placed over `--max-ppi` (default 450) down to `--to-ppi` (default 300), judged by each image's largest placement; for large format try `--max-ppi 200 --to-ppi 150`. JPEGs stay JPEG at their own quality, others are saved losslessly; images with transparency are resampled weighted by it, so no fringe. Palette, 1- and 16-bit, spot/DeviceN images and inline images are listed and left. |
| overprint-spot | Sets dielines (cutting, crease and perf lines, recognised by name as in the Dielines check) to overprint, so the artwork beneath is not knocked out along the cut. `--spot NAME` (repeatable) sets other spots instead, e.g. a varnish or embellishment plate. Each object is bracketed; nothing else changes. |
| remove-layer | Takes everything on a layer off the page and removes the layer (`--layer NAME`, repeatable; default every hidden layer), e.g. a supplier's template. Only the painting goes, so the drawing state other layers rely on is kept. Says so when the layer holds the crop marks and the trim box disagrees with them (run `trim-from-marks+remove-layer`). Verified against the original rendered with the layer off. |
| flatten-annotations | Draws each annotation set to print (stamps, text boxes, drawn shapes, form fields) into the page as it looks now, then takes it off the page, so what prints no longer depends on the RIP. Comment marks (sticky notes, highlights, strike-outs) are left for remove-annotations, as are annotations not set to print. Verified by rendering: the page must look as it did. |
| remove-annotations | Takes every comment, stamp and form field off the page, printing or not. Links are kept; the artwork is not touched. |
| remove-object | Removes one object (`--object PAGE:NUMBER`, numbered as the app's inspector shows them). Easiest from the app: click the page, choose the object, Remove…. Only that object goes, verified against every other object on the page. |
| object-overprint | Sets overprint on or off for one object (`--object PAGE:NUMBER`, numbered as the app's inspector shows them; `--overprint on/off`). Easiest from the app: click the page, choose the object, and use its button. Only that object changes, verified against every other object on the page. Refused for an object inside a form painted more than once, and not allowed in saved action lists. |
| set-boxes | Sets the trim box to the job size (`--trim WxH`, centred on the current trim) and grows the bleed box to at least 3 mm where the page has room. Never shrinks a bleed box. |

With `--compact` (or Compact in the app's Export), the new file is written fresh
with only the objects still in use, which can be much smaller after
rgb-to-cmyk; it is then checked page by page against the full result.

The new file is the original, byte for byte, with the changes appended (a PDF
incremental update, as Acrobat's Save does). It is verified before it is put
in place: original bytes intact, structure sound, only the intended objects
changed, and for add-bleed, the artwork inside the trim renders exactly as
before.

## Checks

| Check | Severity | Rule |
| --- | --- | --- |
| Page size | warn | Reports the trim size. Warns when pages differ from each other or from `--trim` (tolerance 0.5 mm). With `--bound`, warns on an odd page count and notes one that is not a multiple of 4. |
| Blank pages | note / warn | A page with nothing printing inside the trim is a note (often a deliberate inside cover); every page blank warns. |
| Bleed box | fail / warn / note | Fails when the bleed box is under 3 mm, or the page ends at the trim, on an edge where artwork reaches the trim. Only a note when those edges are white: a white edge needs no bleed. Warns when no bleed box is defined but the page extends past the trim. |
| Bleed artwork | fail | Wherever artwork reaches the trim (ink of 5% or more within 0.5 mm), ink must carry on 3 mm past it. Catches clipped artwork, short bleed, gaps, and bleed on some edges only. |
| RGB | warn | Any RGB or Lab object in the printed area. Never a fail. |
| White overprint | fail | Any white object set to overprint. |
| Black overprint | note | 100% black text up to 12 pt or line up to 2 pt that knocks out instead of overprinting. |
| Small text inks | warn / note | Text up to 8 pt in rich black (warn: it should be K only), or in three or more inks (note). |
| Reversed text and lines | warn / note | White text knocked out of two or more inks together (measured on the plates): up to 7 pt warns, 7 to 10 pt is a note. White lines under 0.5 pt out of two or more inks warn. |
| Transparency | note | Objects under 100% opacity or a soft mask, and images with alpha: flattened by the RIP; none allowed in PDF/X-1a. |
| Registration inside trim | fail | Registration colour inside the trim box. Outside the trim it is expected (printer's marks). |
| Blend modes | note | Lists blend modes in use. |
| Blend over same ink | fail / warn / note | A Multiply, Darken or ColorBurn object lying over the same process ink. Fails when the two stack to 200% (solid on solid), warns past 100%, notes below that. Overlaps under 1 mm² are ignored. |
| Total ink | fail | Fails when at least 1 mm² inside the trim is over the limit (`--ink-limit`, default 400%), or any vector or text object is coloured over it. Reports the peak and the area over the limit. |
| Image resolution | warn | Images under 150 ppi at their placed size. Notes images between 150 and 300 ppi. |
| Image detail | warn | Images enlarged from smaller ones (or soft): they pass the resolution check but print soft. Measured from edge sharpness; reports roughly how many ppi of real detail they hold. |
| Safe margin | warn | Live text within 1.5 mm of the trim (`--safe-margin`) or crossing it, and artwork that close to a white edge without reaching it. |
| Hairlines | warn | Stroked lines thinner than 0.25 pt as placed on the page. White lines and non-printing spots are left out. |
| Font embedding | fail | A font used for visible text is not embedded. |
| Trim marks | warn / note | When the crop marks point at a different rectangle from the trim box, the trim is taken from the marks for every other check and the report says it was inferred. Notes when marks and box agree. |
| Dielines | warn / note | Warns when a dieline is not set to overprint. Reports each dieline's size. |
| Spot colours | note / warn / fail | Lists the printing spots in use. Warns on one ink spelt two ways or defined two ways. Fails any printing spot when `--cmyk-only` is given. |
| Annotations | warn / note | Warns on annotations set to print and on form fields. Notes comments that will not print. |
| Layers | warn / note | Warns when a layer is switched off. Lists visible layers. |
| Security | warn | The file is encrypted or restricted. |
| Output intent | note | Reports the print condition the file states, if any. |

Severities: **fail**, **warn**, **note**, and **could not check**. If the tool
meets something it cannot interpret it says so; it does not report a pass it
has not earned.

## Known limits

- **Bleed artwork** tests that ink is present in the bleed, not that it
  continues the same image. Non-printing spot colours (dielines and the like)
  are recognised by name and left out. Spine edges fail unless `--bound` is
  given, because the tool cannot know how a job is bound.
- **Clipping is applied by bounding box.** An object wholly outside the
  clipping region is not judged (it does not print), and a partly clipped one
  is placed where it shows. The clip's exact shape is not followed, so an
  object inside the clip's box but outside a curved clip still counts. Spot
  colours and fonts are still listed for hidden objects: the RIP gets them.
- **Text is measured from the font's glyph widths**, so a run's position covers
  its whole length. Its height is a fixed share of the size (no glyph shapes
  are read). Fonts without a widths table (some standard fonts) and two-byte
  fonts with an encoding other than Identity-H fall back to a one-em box where
  the run starts.
- **Total ink** is measured on the separated plates: the sum of every printing
  plate, process and spot, with overprint honoured. Non-printing spots are
  left out, and RGB objects count through a simple conversion. Registration
  colour is covered by its own check.
- **Hairlines** measures stroked lines only, not thin filled shapes.
- **Safe margin** sees outlined type only on a white edge, not over a
  background that bleeds, and ignores artwork under 15% ink.
- **Blend over same ink** reports blend modes inside pattern fills or knockout
  groups as "could not check". Spot-only objects are not measured, and artwork painted later on top of an
  overlap is not taken into account.
- **Layers** are all treated as visible.

## How it is built

- `content.py` walks each page's content and reports every painted object with
  its colour space, colour, overprint setting and position.
- `checks/` holds one self-contained module per check.
- `engine.py` runs the checks; `report.py` and `cli.py` present the results.

Libraries: [pikepdf](https://pikepdf.readthedocs.io) for file structure,
[PyMuPDF](https://pymupdf.readthedocs.io) for rendering. PyMuPDF is AGPL
licensed, which matters if this tool is ever distributed.

## Tests

```sh
pytest
```

Unit tests build small synthetic PDFs, so they run anywhere. Tests against real
sample files run only when a `samples/` folder with an `expected.json` is
present. **Sample PDFs are client artwork and must never be committed**; the
`.gitignore` excludes all PDFs.
