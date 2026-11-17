# Status and next tasks

## Agreed check rules

| Check | Severity | Rule |
| --- | --- | --- |
| Page size | warn | Pages differ from each other, or from the job spec (`--trim`), by more than 0.5 mm |
| Bleed box | fail | Under 3 mm, or the page ends at the trim, on an edge where artwork reaches the trim |
| Bleed box | note | As above, but no artwork reaches those edges: a white edge needs no bleed |
| Bleed box | warn | No bleed box defined but the page extends past the trim |
| Bleed artwork | fail | Artwork reaches the trim (ink within 0.5 mm of it) and does not carry on 3 mm past it |
| RGB | warn | Any RGB object. Common in print files; never a fail |
| White overprint | fail | Always |
| Registration inside trim | fail | Always. Outside the trim is fine |
| Blend modes | note | Listed quietly; common in print files |
| Blend over same ink | fail | A Multiply, Darken or ColorBurn object lies over the same process ink and the two stack to 200% (solid on solid), over at least 1 mm² |
| Blend over same ink | warn | As above, stacking past 100% |
| Blend over same ink | note | As above, staying within 100% |
| Total ink | fail | Over the limit across at least 1 mm², or any vector or text object coloured over it. Limit is `--ink-limit`, default 400% until the press limit is known |
| Image resolution | warn | Under 150 ppi. 300 preferred; large format may sit lower |
| Safe margin | warn | Live text within 1.5 mm of the trim or crossing it; or artwork of 15% ink or more within 1.5 mm of a white edge without reaching it. `--safe-margin` sets the distance |
| Hairlines | warn | Stroked line under 0.25 pt as placed |
| Font embedding | fail | A font used for visible text is not embedded |
| Trim marks | warn | Crop marks point at a rectangle that differs from the trim box by more than 0.5 mm. A note when they agree |
| Dielines | warn | A dieline (by spot name) is not set to overprint. Its size is reported as a note |
| Annotations | warn | Annotations set to print (stamps, comments), or any form field. Links are ignored |
| Annotations | note | Comments not set to print |
| Layers | warn | Any layer switched off. Visible layers are listed as a note |
| Security | warn | File is encrypted or carries restrictions |
| Output intent | note | Reports the stated print condition, or that there is none |

Non-printing spot colours (dielines, embellishment layers) must never cause a fail.
They are recognised by name (list in `edges.py`: dieline, cutcontour, crease,
perf, skodix, varnish, spot uv, foil, emboss, non-viewable, template and
similar). A spot with a name not on the list counts as printing ink, so add
names as real jobs turn them up.

## Results on the 14 sample files (v0.1)

All agree with the owner's verdicts. Two clean files and one correct label
pass. Known faults found: no bleed, RGB, white overprint (catalogue pages 48
and 49), 66 ppi images, 400% ink, crop marks inside the trim box.

## The pull-up banner (check added 2026-11-17)

What is in the file, checked on the original that went to the RIP: a 100% K
rectangle painted Normal, then a transparency group holding one 100% K
rectangle, the group painted with Multiply. They overlap for 756 mm of the
banner's height, full width. The photo sits above the black area and plays no
part.

Blended correctly (the page blends in DeviceCMYK) the overlap is plain 100% K,
which is why Acrobat's canvas looked right and a rendered ink measurement finds
nothing. The 200% came from how the RIP handled the Multiply. Which RIP, and
exactly what it did, is not yet confirmed.

So the check does not look at the blended result. For each blend-mode object it
renders the page as painted before the object, and the object alone as Normal,
and adds the two per ink where both have ink. The banner reports "Black stacks
to 200% across 0.65 m²". The renders use an in-memory copy of the page that is
never saved (approved by the owner; the input stays byte-identical).

Known limits of the check:

- Blend modes inside placed or grouped artwork are measured (each containing
  form is cut off at the object, in memory). Those inside pattern fills or
  knockout groups are reported as "could not check".
- Objects in spot ink only are not measured (dielines set to Multiply must
  never fail). Spot inks under a blend object count through their CMYK
  equivalents.
- Artwork painted later on top of the overlap is not taken into account.
- Only Multiply, Darken and ColorBurn are measured.

On the other samples: no file changed status. The wobblers file gains a warn
(Yellow 119%). The catalogue gains a warn on 14 pages, worst Black 195% on
page 8. That object is an InDesign effect: a 100% K rectangle with a soft mask,
set to Multiply, darkening a photo that already carries 40 to 95% K. It is a
deliberate darkening and a warn is the right level, but it shows the warn will
fire on this common InDesign construction.

The banner's entry in `samples/expected.json` was corrected on 2026-11-17. It
had expected a Bleed box warn, which belongs to a re-saved copy with a margin
around the page. The original ends at the trim, so it is a Bleed box fail. The
entry now also requires the Blend over same ink fail. All 14 samples agree.

## Bleed artwork (check added 2026-11-17)

Rule from the owner: an edge that is white at the trim needs no bleed, even if
the file has none.

How it measures: one rendered strip per edge. Along the edge, wherever ink of
5% or more comes within 0.5 mm of the trim, ink must carry on unbroken for
3 mm past it. Three things were learned from the samples and built in:

- A 2% background tint is not artwork: under 5% at the trim counts as white.
- A fade that thins out past the trim still bleeds: past the trim anything over
  1.5% counts.
- Artwork with a slanted side still bleeds, although straight out from the trim
  the ink stops short near that side. Ink may sit up to twice the distance out
  to either side. A square-ended gap is still found.

Non-printing spots are silenced in an in-memory copy before measuring, so a
dieline on the trim does not read as artwork and a dieline over the sheet does
not stand in for missing bleed. Stretches under 1 mm are ignored.

Spine edges: single pages exported from a facing-pages layout have no bleed at
the spine. The catalogue has six such pages (21, 27, 33, 37, 44, 46), which
fail by default. `--bound` exempts the spine edge of each page (page 1 taken as
a right-hand page). The tool cannot know the binding, so the default stays
strict and the report suggests `--bound` when every short edge is a spine edge.
Bound is deliberately not the default for multi-page files: the place cards
(114 pages) and stamp cards (49 pages) are loose items that need bleed on every
edge, and exempting their spine side would be a pass they had not earned.

Limits: it tests that ink is there, not that the bleed continues the same
image. Right-to-left binding is not handled by `--bound`.

Changes to agreed results in `samples/expected.json` (the previous file is kept
as `samples/expected.before-bleed-check.json`):

- **5. A4 PBR**: was FAIL (Bleed box fail), now PASS. Nearest ink is 10 mm
  from the page edge.
- **68pp Yearbook**: was FAIL (Bleed box fail), now WARN (RGB, resolution). No
  page has ink closer than 3 mm to the page edge. That is tight for a safe
  margin, which is a different check (see next tasks).
- Bleed artwork fail is now required for the banner, place cards, Little Stars
  and X-am posters, and forbidden for the six files that bleed properly or
  have white edges.

Both status changes follow from the owner's rule. The owner left the call to
Claude on 2026-11-17 and they stand.

## Other checks added 2026-11-17

Built while the owner was away. The owner left the severities to Claude
(2026-11-17): hairlines warn, because thin lines are common and often
intentional; unembedded fonts fail, because the RIP substitutes another font.

- **Total ink** now reports the area over the limit and where, and takes the
  limit from `--ink-limit`. It leaves non-printing spots out (the banner's old
  158% reading was the template spot, not artwork). It also checks each vector
  and text object's own colour: the X-am posters have 1,160 fine objects set in
  400% ink that cover only 11 mm² of rendered pixels, so the area rule alone
  nearly missed them. At a 300% limit the X-am posters have 28 cm² over and the
  banner's photo 648 mm².
- **Hairlines.** Wobblers: one line at 0.23 pt. Catalogue: 68 on pages 46 and
  47, thinnest 0.20 pt. Little Stars: 828 across nine pages, thinnest 0.01 pt
  (Canva illustrations). No sample changed status. Only stroked lines are
  measured, not thin filled shapes.
- **Font embedding.** Every font used in the samples is embedded, so the fail
  is proven only on synthetic files.

## Safe margin (check added 2026-11-17)

The owner left the distance to Claude. It is 1.5 mm by default because the
Tempest label, an agreed PASS, has a printed black border about 1.8 mm inside
the trim: a 3 mm rule would have contradicted that verdict. Use
`--safe-margin 3` (or 5) for books and brochures.

Two measurements: live text (word boxes from the renderer), and ink on a white
edge that comes inside the margin without reaching the trim. Artwork that
reaches the trim is the bleed checks' business. Built in from the samples:

- A pale fade near the edge is not an object (catalogue pages 3 to 7, about 6%
  ink): margin artwork must be 15% or more.
- Display type over 25 mm that runs off the page is deliberate (the catalogue's
  section pages) and is not reported as cut text.

Results: Little Stars has one word per page touching the bottom trim (all 49
pages) and one word 0.8 mm from it. Nothing else is flagged at 1.5 mm. At 5 mm
the yearbook would be flagged on most pages (text 3.2 mm from the trim).

Limits: outlined type is only seen on a white edge, not over a background that
bleeds. Word boxes include the full line height.

## Document-level checks (added 2026-11-17)

Annotations, Layers, Security and Output intent. Severities are Claude's call
under the owner's delegation: none is a fail, because each depends on how the
RIP treats it. The samples barely exercise them (one link annotation, two files
with visible layers, two with an output intent), so they are proven on
synthetic files. An encrypted file that opens without a password is still
measured in full by every other check.

Known inconsistency, stated in the Layers check: checks that read page content
look at hidden layers, checks that work from a render do not.

## Trim marks and dielines (added 2026-11-17)

Trim marks: corner crop marks in registration colour are found from the page
content and the rectangle they point at is compared with the trim box. Eight
samples have marks that agree.

**The banner's marks do not agree, and this matters.** Its crop marks point at
845.0 × 2100.0 mm, 7.4 mm inside the 859.8 × 2114.8 mm trim box (which equals
the page). So the trim box is almost certainly wrong, the artwork probably has
7.4 mm of bleed, and the banner's two "no bleed" fails and its "registration
inside trim" fail all follow from the wrong box, not from the artwork. The
owner's original verdict for the banner was a Bleed box warn, which fits.
**Question for the owner:** when marks and trim box disagree, should the tool
measure from the marks? Until decided it measures from the trim box and warns.

Dielines: the wobblers file has a dieline of 148.0 × 208.0 mm inside an A4
trim box, set to overprint. The size is the outer bounds of the dieline
objects, not a measured cutting path.

## Next tasks, in order

1. **Banner follow-ups.** The RIP that printed the banner is not known
   (owner, 2026-11-17), so what it did with the Multiply stays an inference.
   Watch whether the warn level is too noisy on InDesign jobs.
2. **Safe margin: confirm the distance.** 1.5 mm is the default; the owner may
   want a larger one per product type.
3. **Total ink: set the real press limit.** The area report and `--ink-limit`
   are done. The owner does not have the press limits yet (2026-11-17), so the
   default stays at 400% as a placeholder.
4. **Compare against Acrobat and PitStop** on real jobs; investigate every
   disagreement.
5. **Decide what to measure from when trim marks and trim box disagree** (the
   banner). Then correct the banner's entry in `expected.json` to match.
6. Later phases: page boxes, trim marks and scaling; bleed fixes; spot colour
   tools; action lists; desktop interface; object tree and editing.

## Interface model (for later)

One canvas, four states: inspect (read-only), propose (a fix shown as a
before/after preview), review (pending changes list), export (always a new
file). Checks and fixes are separate kinds of operation in the engine.
