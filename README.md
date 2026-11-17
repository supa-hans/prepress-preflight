# prepress-preflight

A read-only command-line preflight checker for print PDFs. Give it a PDF and it
prints a report. It never writes to the file it is given.

This is the engine for a larger pre-press tool. Fixes, an interface and action
lists come later and will sit on top of it.

## Two rules the project is built on

1. **No change without confirmation.** This tool only inspects.
2. **Nothing altered beyond what was requested.** Checks cannot modify a file;
   the test suite verifies the input is byte-identical after a run.

## Install

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Use

```sh
preflight artwork.pdf                 # readable report
preflight artwork.pdf --trim 90x55    # also compare trim size with the job spec (mm)
preflight artwork.pdf --json          # same findings as data
preflight *.pdf                       # several files
```

Exit code: `0` no fails, `1` at least one fail, `2` a file could not be read.

## Checks

| Check | Severity | Rule |
| --- | --- | --- |
| Page size | warn | Reports the trim size. Warns when pages differ from each other or from `--trim` (tolerance 0.5 mm). |
| Bleed box | fail / warn | Fails when the bleed box is under 3 mm on any edge, or the page ends at the trim. Warns when no bleed box is defined but the page extends past the trim. |
| RGB | warn | Any RGB or Lab object in the printed area. Never a fail. |
| White overprint | fail | Any white object set to overprint. |
| Registration inside trim | fail | Registration colour inside the trim box. Outside the trim it is expected (printer's marks). |
| Blend modes | note | Lists blend modes in use. |
| Total ink | fail | Fails when at least 1 mm² inside the trim reaches 400%. Reports the highest coverage. |
| Image resolution | warn | Images under 150 ppi at their placed size. Notes images between 150 and 300 ppi. |

Severities: **fail**, **warn**, **note**, and **could not check**. If the tool
meets something it cannot interpret it says so; it does not report a pass it
has not earned.

## Known limits

- **Bleed** is judged from the bleed box only. Whether artwork actually fills
  the bleed is a separate, later check.
- **Clipping is not applied.** An object counts where it is drawn, even if a
  clipping mask hides it.
- **Text positions are approximate.** No font metrics are read.
- **Total ink** is measured on a rendered page. Overprint is not simulated,
  spot colours count through their CMYK equivalents, and RGB objects through a
  simple conversion. Registration colour is covered by its own check.
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
