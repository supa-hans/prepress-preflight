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
preflight artwork.pdf --json          # same findings as data
preflight *.pdf                       # several files
```

Exit code: `0` no fails, `1` at least one fail, `2` a file could not be read.

## Checks

| Check | Severity | Rule |
| --- | --- | --- |
| Page size | warn | Reports the trim size. Warns when pages differ from each other or from `--trim` (tolerance 0.5 mm). |
| Bleed box | fail / warn / note | Fails when the bleed box is under 3 mm, or the page ends at the trim, on an edge where artwork reaches the trim. Only a note when those edges are white: a white edge needs no bleed. Warns when no bleed box is defined but the page extends past the trim. |
| Bleed artwork | fail | Wherever artwork reaches the trim (ink of 5% or more within 0.5 mm), ink must carry on 3 mm past it. Catches clipped artwork, short bleed, gaps, and bleed on some edges only. |
| RGB | warn | Any RGB or Lab object in the printed area. Never a fail. |
| White overprint | fail | Any white object set to overprint. |
| Registration inside trim | fail | Registration colour inside the trim box. Outside the trim it is expected (printer's marks). |
| Blend modes | note | Lists blend modes in use. |
| Blend over same ink | fail / warn / note | A Multiply, Darken or ColorBurn object lying over the same process ink. Fails when the two stack to 200% (solid on solid), warns past 100%, notes below that. Overlaps under 1 mm² are ignored. |
| Total ink | fail | Fails when at least 1 mm² inside the trim is over the limit (`--ink-limit`, default 400%), or any vector or text object is coloured over it. Reports the peak and the area over the limit. |
| Image resolution | warn | Images under 150 ppi at their placed size. Notes images between 150 and 300 ppi. |
| Safe margin | warn | Live text within 1.5 mm of the trim (`--safe-margin`) or crossing it, and artwork that close to a white edge without reaching it. |
| Hairlines | warn | Stroked lines thinner than 0.25 pt as placed on the page. White lines and non-printing spots are left out. |
| Font embedding | fail | A font used for visible text is not embedded. |
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
- **Clipping is not applied.** An object counts where it is drawn, even if a
  clipping mask hides it.
- **Text positions are approximate.** No font metrics are read.
- **Total ink** is measured on a rendered page. Overprint is not simulated,
  printing spot colours count through their CMYK equivalents, and RGB objects
  through a simple conversion. Non-printing spots are left out. Registration
  colour is covered by its own check.
- **Hairlines** measures stroked lines only, not thin filled shapes.
- **Safe margin** sees outlined type only on a white edge, not over a
  background that bleeds, and ignores artwork under 15% ink.
- **Blend over same ink** reports blend modes inside pattern fills or knockout
  groups as "could not check". Spot-only objects are not measured, and artwork painted later on top of an
  overlap is not taken into account.
- **Upscaled images** pass the resolution check: the number cannot show that an
  image was enlarged from a poor original.
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
