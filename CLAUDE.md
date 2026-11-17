# prepress-preflight

Read-only preflight checker for print PDFs, and the engine for a larger
pre-press tool (a modern alternative to Acrobat + PitStop Pro). The owner is a
pre-press operator; explain the "why" behind recommendations and verify claims
before stating them.

## Hard rules

1. **No change to a print file without the user's express confirmation.**
2. **Never alter a supplied file beyond what was requested.** Checks are
   read-only. Future fixes are proposed operations with a before/after preview
   and always write a new file.
3. **Never commit PDFs.** Samples are client artwork and live in `samples/`
   (gitignored), with `samples/expected.json` holding the agreed results.
4. **Fail loudly.** If a check cannot interpret something it reports
   "could not check". It never reports a pass it has not earned.
5. **Clean-room only.** Learn from PitStop by using it and by comparing files
   before and after. Do not decompile it or use notes derived from its binaries.
6. **No AI vision for checks.** Results must be repeatable, and client artwork
   stays on the machine.

## Working here

- Install: `pip install -e ".[dev]"`. Test: `pytest`. Run: `preflight file.pdf`.
- Each check is one module in `src/prepress_preflight/checks/`, built on the
  events from `content.py`. Add a unit test on a synthetic PDF (see
  `tests/conftest.py`) for every rule.
- If a sample result disagrees with `expected.json`, investigate before
  changing either. Expectations have been wrong before.
- Stack: pikepdf (structure), PyMuPDF (rendering, with colour management off
  so CMYK values are preserved). PyMuPDF is AGPL.

See `docs/STATUS.md` for agreed rules, results so far and the next tasks.
