# Status and next tasks

## Agreed check rules

| Check | Severity | Rule |
| --- | --- | --- |
| Page size | warn | Pages differ from each other, or from the job spec (`--trim`), by more than 0.5 mm |
| Bleed box | fail | Under 3 mm on any edge, or the page ends at the trim |
| Bleed box | warn | No bleed box defined but the page extends past the trim |
| RGB | warn | Any RGB object. Common in print files; never a fail |
| White overprint | fail | Always |
| Registration inside trim | fail | Always. Outside the trim is fine |
| Blend modes | note | Listed quietly; common in print files |
| Total ink | fail | Reaches 400%. Real press limit still to be supplied |
| Image resolution | warn | Under 150 ppi. 300 preferred; large format may sit lower |

Non-printing spot colours (dielines, embellishment layers) must never cause a fail.

## Results on the 14 sample files (v0.1)

All agree with the owner's verdicts. Two clean files and one correct label
pass. Known faults found: no bleed, RGB, white overprint (catalogue pages 48
and 49), 66 ppi images, 400% ink, crop marks inside the trim box.

## Known gap: the pull-up banner

Two black objects overlap; one is set to Multiply and the other is not, and the
job printed 200% black where they overlap. It did not show on Acrobat's normal
canvas, only in the Flattener Preview, macOS Quick Look and a browser. The tool
measures 158% ink and reports only "Blend mode in use: Multiply". Catching this
is a priority: work out exactly what is in the file, then decide on a check
(for example, a blend-mode object overlapping other ink of the same colourant).

## Next tasks, in order

1. **Banner fault** (above).
2. **True-bleed check.** Render the strip between trim and bleed with
   transparency kept, and test whether artwork is actually painted there on
   each edge. Must catch clipped artwork, partial bleed and bleed on some edges
   only. A white trim edge needs no bleed. Canva files are the main target.
3. **Total ink against a real press limit**, and report the area over the limit.
4. **Compare against Acrobat and PitStop** on real jobs; investigate every
   disagreement.
5. Later checks: font embedding, hairlines under 0.25 pt, trim mark detection,
   dieline measurement, annotations, layers, security, output intent.
6. Later phases: page boxes, trim marks and scaling; bleed fixes; spot colour
   tools; action lists; desktop interface; object tree and editing.

## Interface model (for later)

One canvas, four states: inspect (read-only), propose (a fix shown as a
before/after preview), review (pending changes list), export (always a new
file). Checks and fixes are separate kinds of operation in the engine.
