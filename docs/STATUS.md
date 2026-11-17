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
| Trim marks | warn | Crop marks point at a rectangle that differs from the trim box by more than 0.5 mm. The trim is then taken from the marks for every other check, and the report says so. A note when they agree |
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

**The banner's marks do not agree with its trim box.** Its crop marks point at
845.0 × 2100.0 mm, 7.4 mm inside the 859.8 × 2114.8 mm trim box (which is just
the page). Measured from the box, the banner failed on bleed twice and on
registration inside the trim; all three came from the wrong box.

Owner's decision (2026-11-17): when marks and trim box disagree, measure from
the marks, and tell the user the trim was inferred. The engine does this before
any check runs (`marks.infer_trim`). The report then carries a Trim marks
warning ("Trim inferred from the crop marks: ... Bleed, margins, ink and
registration are measured from the marks") and a Page size note. The file's own
box is kept as `page.declared_trim`. Marks that point outside the page are
ignored.

The banner now has one fail, the Multiply fault, plus the Trim marks warning.
Its entry in `expected.json` was corrected to match (previous file kept as
`samples/expected.before-trim-from-marks.json`). The owner's first verdict for
the banner, before the original file came in, was a bleed warn and not a fail,
which fits.

Dielines: the wobblers file has a dieline of 148.0 × 208.0 mm inside an A4
trim box, set to overprint. The size is the outer bounds of the dieline
objects, not a measured cutting path.

## Audit of 2026-11-17

All checks were run on the same artwork placed four ways: plain, rotated 90
and 180 degrees, with the page origin offset, and with a UserUnit of 2 (the
scaling very large formats use). Rotation and offset were right. UserUnit was
wrong: areas came out 4 times too large and lengths twice too long, because
the renderer already applies the scale and the code applied it again. This
was also in the original total ink check. Fixed, with tests in
`tests/test_user_unit.py`. No sample file uses a UserUnit, so no result
changed.

All 14 samples run with no "could not check" findings. The slowest is the
509 MB banner at about 11 seconds.

## First fix: trim box from crop marks (added 2026-11-18)

Approved by the owner as proposed. `preflight fix trim-from-marks file.pdf`
shows what would change and runs preflight before and after (on a temporary
copy that is deleted). It writes only with `--write NEW.pdf` and a typed "yes"
(or `--yes` for scripts), never over the input or any existing file.

It changes only TrimBox (to where the crop marks point) and BleedBox (the new
trim plus 3 mm, `--min-bleed`, kept within the page). On the banner: trim
845.0 × 2100.0 mm, bleed box 851.0 × 2106.0 mm. The crop marks stop 2.1 mm
outside the trim, so the 3 mm bleed box takes in 0.9 mm of each mark's tip, in
the area that is cut away.

How it writes: an incremental update. The new file is the original's bytes,
unchanged, followed by the changed page dictionaries and a small
cross-reference section (classic table or cross-reference stream, matching the
original). Before the new file is put in place it is verified: the original
bytes are intact, the PDF structure is sound, the changed objects hold exactly
the intended change, and every other object is the same as in the original.
If any of that fails, nothing is written.

On the real banner the new file is 767 bytes longer, preflight reports one
fail (the Multiply fault) and "Trim marks agree with the trim box", and macOS
Quick Look renders it.

Bug found on the way: the first version of the verification compared object
references rather than contents, so "every other object is the same" was
always true. Fixed and covered by a test before anything was committed.

Limits: encrypted files are refused. Pages with a UserUnit are left alone.
An appended update ends linearisation ("fast web view"), which does not matter
for print. Structural warnings already in the original (the wobblers file has
a stream that does not decode cleanly and stale linearisation hints) do not
block a fix; only problems the update itself introduces do.

The fixed banner still shows the Multiply fault in Preview and Acrobat's
Flattener Preview, as expected: this fix changes the boxes, not the artwork.
A fix for the fault itself (setting that group back to Normal) is parked at
the owner's request (2026-11-18).

## Second fix: set page boxes (added 2026-11-18)

`preflight fix set-boxes file.pdf [--trim WxH] [--min-bleed MM]`. Sets the
trim to the job size, centred where the current trim is and turned to match
its orientation, and makes sure the bleed box gives at least the minimum
bleed. Rules learned from the samples:

- It never shrinks a bleed box that already gives more (the catalogue's 5 mm,
  the business card's 7.4 mm), and only grows the short edges. When the trim
  itself is reset, the old bleed box is replaced, since it was drawn around
  the wrong trim.
- It does not write a box whose value would not change what the file means,
  for example a missing trim box set to the page size.
- A page that ends at the trim cannot gain bleed by changing boxes. It says so
  instead of proposing anything, and when crop marks disagree with the trim
  box it points to `trim-from-marks` instead.

On the samples, only the wobblers file gets a change: a 3 mm bleed box where
there was none, which clears its "Bleed box not defined" warning.

## Third fix: add bleed (added 2026-11-18)

Approved as proposed. `preflight fix add-bleed file.pdf [--method mirror|stretch]
[--min-bleed MM] [--bound]`. For each edge where artwork reaches the trim
without bleed, the strip just inside the trim is rendered (300 ppi, 150 ppi
for pages over 1 m) in the edge artwork's own colour space, reflected outward,
and placed as an image beneath the original artwork, clipped to outside the
trim. The page grows where it has no room; the old size becomes the TrimBox
and the BleedBox is trim + 3 mm. The original content stream is untouched.

Learned while building it, all now handled and tested:

- **Overshoot.** Bleed made to exactly 3 mm read as 2.7 mm on the check's pixel
  grid, so the made bleed runs 1 mm past the bleed box (4 mm in all).
- **Thin edge artwork.** A border or bar narrower than the bleed mirrors to a
  narrow band and then white (X-am right edge). Where the mirror would leave a
  gap, the colour at the trim is carried on instead.
- **Artwork stopping a hair short.** The pixel at the trim straddles the cut,
  and artwork often stops just short, so the carried colour is taken from the
  nearest ink within 1 mm of the trim.
- **A white sliver inside the trim cannot be fixed by bleed.** Little Stars
  pages 12 and 13 have a pale shape stopping about 0.3 mm short of the trim
  for 1.2 mm; a hairline of white may show after cutting. The fix must not
  touch artwork inside the trim, so up to 2 mm of such near-misses per edge is
  accepted and stays visible as a Bleed artwork fail in the report.
- **File size.** Identical bleed strips are stored once and shared between
  pages: the place cards went from 62 MB to 3.2 MB (original 2.4 MB).

Verification before the new file is put in place, on top of the append
checks: the area inside the trim must render identically before and after
(ignoring the one row of pixels that straddles the cut), and every filled
edge must pass the Bleed artwork check.

Results: place cards FAIL -> PASS. Little Stars: no-bleed fail on every page
-> a 1 mm near-miss on pages 12 and 13 (still FAIL for that, and RGB as
before; the bleed strips are RGB to match the artwork, so the RGB image count
rises). X-am posters: bleed fixed, still FAIL for 400% ink. Catalogue: only
its six spine-side pages would be filled, and none with `--bound`.

On screen, Preview and Acrobat may show a hairline where the bleed image meets
the artwork at the trim. It is anti-aliasing in the viewer, not ink: sampled
values on both sides of the trim match the original.

Bug found on the way: pikepdf's structure check, run on a linearised file,
leaves the process silently discarding everything written to stderr. Error
messages from the fix command vanished. The check now runs in a separate
process.

## Add bleed: clip to bleed box and trim marks (added 2026-11-18)

Asked for by the owner after checking the bled files ("the bleed looks good on
all three"). The owner confirmed that removing and adding trim marks is not a
destructive action and the tool may do it.

- `--clip-to-bleed` clips everything on the page to the bleed box, by wrapping
  the existing content in a clip; the original content stream is not edited.
  Verified by rendering the page without the new marks: nothing may be painted
  outside the bleed box.
- `--marks` adds eight trim marks in registration colour, 0.25 pt on a 1.25 pt
  white keyline, 5 mm long, starting at the bleed box edge and lying along the
  trim edges. The page grows to 9 mm round the trim (3 mm bleed + 5 mm mark +
  1 mm). Existing crop marks are removed first: only registration-colour
  straight lines lying along a trim edge, outside the trim. For that, the
  page's own content is rewritten without those strokes (the old streams stay
  in the file, unused). Marks inside a placed graphic leave the page alone.
  Verified: exactly one set of eight marks, pointing at the trim.

On the samples (both options): place cards FAIL -> PASS; Little Stars FAIL ->
WARN; X-am posters still FAIL for 400% ink only. Banner, after trim-from-marks:
its old marks are replaced, everything is clipped to 3 mm of bleed, and the
only fail left is the Multiply fault.

Two weaknesses in the Bleed artwork check were found and fixed on the way. No
sample verdict changed:

- Bleed of exactly 3 mm (the usual case, and what the clip produces) read as
  2.7 mm, because the measuring strip ended exactly at 3 mm and lost its last
  pixel. The strip now runs 0.5 mm deeper, and ink running to the edge of a
  page that gives the full bleed counts as full bleed.
- Where two shapes end on the trim (a white band over a coloured background),
  anti-aliasing leaks a one-pixel hairline of the lower colour, which read as
  artwork reaching the trim. The pixel either side of the trim is now left out.

Limit found: Little Stars' 0.3 mm near-miss (pages 12 and 13) is at the edge
of what the check can resolve. Whether it is reported depends on where the
pixel grid falls, so it no longer shows. Slivers under about 0.3 mm are not
reliably detected.

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
5. Later checks: none outstanding from the original list.
6. Later phases: adding trim marks and scaling (both change artwork, so
   propose first); a fix for the banner's Multiply fault (parked by the owner); spot colour
   tools; action lists; desktop interface; object tree and editing.

## Interface model (for later)

One canvas, four states: inspect (read-only), propose (a fix shown as a
before/after preview), review (pending changes list), export (always a new
file). Checks and fixes are separate kinds of operation in the engine.
