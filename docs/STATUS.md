# Status and next tasks

## Agreed check rules

| Check | Severity | Rule |
| --- | --- | --- |
| Page size | warn | Pages differ from each other, or from the job spec (`--trim`), by more than 0.5 mm |
| Page size | warn | Bound job (`--bound`) with an odd page count |
| Page size | note | Bound job with an even count that is not a multiple of 4 (saddle stitching would add blanks; perfect binding is fine) |
| Blank pages | note | A page with no printing ink inside the trim (marks outside, white, invisible text and non-printing spots do not count). Often deliberate, e.g. an inside cover |
| Blank pages | warn | Every page is blank |
| Bleed box | fail | Under 3 mm, or the page ends at the trim, on an edge where artwork reaches the trim |
| Bleed box | note | As above, but no artwork reaches those edges: a white edge needs no bleed |
| Bleed box | warn | No bleed box defined but the page extends past the trim |
| Bleed artwork | fail | Artwork reaches the trim (ink within 0.5 mm of it) and does not carry on 3 mm past it |
| RGB | warn | Any RGB object. Common in print files; never a fail |
| CMYK profiles | note | CMYK tagged with an embedded ICC profile (named). Whether it is converted depends on the RIP |
| White overprint | fail | Always |
| Black overprint | note | 100% black (C0 M0 Y0 K100 or a Black separation) text up to 12 pt or line up to 2 pt that knocks out, or overprints in mode 0 (which overprints nothing in CMYK). The overprint-black fix uses the same rules |
| Small text inks | warn | Text up to 8 pt as placed in rich black (K 85% or more with any C, M or Y at 10% or more): it should be K only |
| Small text inks | note | Other text up to 8 pt in three or more inks (10% or more each); may be a deliberate brand colour |
| Reversed text and lines | warn | White text up to 7 pt as placed, knocked out of two or more printing plates at 30% or more each, together on at least 40% of the text's box (measured on the separations, spots included) |
| Reversed text and lines | note | The same from 7 up to 10 pt |
| Reversed text and lines | warn | A white stroked line under 0.5 pt as placed, out of two or more inks as above. A white stroke outlining a white-filled shape is not a line |
| Registration inside trim | fail | Always. Outside the trim is fine |
| Blend modes | note | Listed quietly; common in print files |
| Transparency | note | Objects under 100% opacity (and invisible ones at 0%), under a soft mask, or images with an alpha channel. Listed, never judged: it depends on the RIP, and PDF/X-1a allows none |
| Blend over same ink | fail | A Multiply, Darken or ColorBurn object lies over the same process ink and the two stack to 200% (solid on solid), over at least 1 mm² |
| Blend over same ink | warn | As above, stacking past 100% |
| Blend over same ink | note | As above, staying within 100% |
| Total ink | fail | Over the limit across at least 1 mm², or any vector or text object coloured over it. Limit is `--ink-limit`, default 400% until the press limit is known |
| Image resolution | warn | Under 150 ppi. 300 preferred; large format may sit lower |
| Image resolution | note | Over 450 ppi (Acrobat's and PitStop's usual threshold): more than the press can use; downsample-images would make the file smaller |
| Image detail | warn | An image at 150 ppi or more whose edges are as soft as an image enlarged about 2x or more (edge sharpness under 0.38); reports the real detail in ppi |
| Safe margin | warn | Live text within 1.5 mm of the trim or crossing it; or artwork of 15% ink or more within 1.5 mm of a white edge without reaching it. `--safe-margin` sets the distance |
| Hairlines | warn | Stroked line under 0.25 pt as placed |
| Font embedding | fail | A font used for visible text is not embedded |
| Trim marks | warn | Crop marks point at a rectangle that differs from the trim box by more than 0.5 mm. The trim is then taken from the marks for every other check, and the report says so. A note when they agree |
| Dielines | warn | A dieline (by spot name) is not set to overprint. Its size is reported as a note |
| Spot colours | note | Every printing spot in use, with what uses it. Non-printing spots listed separately |
| Spot colours | warn | One ink spelt more than one way ("PANTONE 185 C" / "Pantone 185C"), or one name defined two ways |
| Spot colours | fail | Any printing spot, when the job is CMYK only (`--cmyk-only`) |
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

## Fourth fix: add marks (added 2026-11-18)

`preflight fix add-marks file.pdf [--clip-to-bleed]`: the same trim marks as
`add-bleed --marks`, with no bleed made, for files that already bleed or need
none. Missing bleed is left as it is and still reported.

Existing marks that point at the trim are kept, not replaced like with like.
Applications put marks about 2.1 mm from the trim, so they normally reach into
a 3 mm bleed; that is standard. The exception is when clipping to the bleed box
is asked for, since the clip would cut old marks down to stubs: then they are
replaced, or the page is left alone if they are inside a placed graphic.
The same rule now applies to `add-bleed --marks`.

On the samples, only files without marks get them: place cards, A4 PBR,
Oliver Hume, the yearbook, Little Stars and the X-am posters. The eight files
with marks are left as they are.

## Fifth fix: scale (added 2026-11-18)

`preflight fix scale file.pdf --trim WxH [--fill | --fit]`. Scales the whole
page about the centre of its trim: the artwork (wrapped in a scaling
instruction, not edited), every page box, and link and annotation positions.

- Within 1% of the job's shape it scales to the exact size and reports the
  small distortion per axis. Further out it is refused unless `--fill` (cover
  the trim and crop the overflow, reported in mm per side) or `--fit` (fit
  inside, white margins reported) is chosen. It never distorts more than 1% on
  its own.
- The bleed box becomes the new trim plus the original bleed, scaled.
- With `--fill` or `--fit`, scaled crop marks would mark the old shape, so they
  are removed and new ones drawn for the new trim. If they are inside a placed
  graphic, the page is left alone with the reason. The LC business card at
  85 x 55 is the example: its marks are in a placed graphic.
- Verified by rendering the original at the matching scale and comparing it
  with the new trim area, allowing only for resampling differences.

Pull-up banners for the owner's usual supplier are 845 x 2100 mm trim (owner,
2026-11-18), so the banner sample was not supplied at the wrong size.

Bug found while building it, now fixed everywhere: PyMuPDF's page matrix
ignores the page origin on rotated pages, and its rotation step uses the
unscaled size on UserUnit pages. Every check and fix that maps page positions
to rendered pixels used it. Pages made by add-bleed have a non-zero origin, so
a rotated page run through add-bleed would have been mis-measured afterwards.
None of the 14 samples is rotated, so no result changed. `device_matrix` in
document.py now builds the mapping from the crop box, and
`tests/test_device_matrix.py` checks all 16 combinations of rotation, crop box
and UserUnit.

## Action lists (added 2026-11-18)

Fixes join with `+` and run in order: `preflight fix trim-from-marks+add-marks
banner.pdf --clip-to-bleed --write press.pdf`. Each step is proposed on the
result of the step before and written, with its own verification, to a
temporary file. The user sees every step's proposal and one before/after
preflight for the whole chain, and confirms once. Only then is the result
copied to the new file, after checking again that the original's bytes are
intact at its start and that its structure is sound. A step with nothing to do
is shown and passed over; a step that fails stops the chain with nothing
written. Options apply to the steps they belong to.

Examples on the samples (originals unchanged):

- Banner, `trim-from-marks+add-marks --clip-to-bleed`: trim corrected, clipped
  to 3 mm of bleed, old marks replaced. Only the Multiply fail remains. About
  43 seconds for the 509 MB file.
- Little Stars, `scale+add-bleed --trim 210x297 --clip-to-bleed --marks`:
  scaled to A4, bled, clipped and marked in one go: FAIL -> WARN.

### Saved action lists (added 2026-11-18)

A chain can be saved under a name and run with `@name`:

    preflight actions save banner-press "trim-from-marks" "add-marks --clip-to-bleed" \
        --description "Pull-up banners: trim from the marks, clip, fresh marks"
    preflight fix @banner-press job.pdf --write press.pdf

A list is a plain text file, one step per line with that step's own options,
in `~/.config/prepress-preflight/actions/` (or the folder in
`PREFLIGHT_ACTIONS`), so it can be read, edited and shared. A list is checked
when saved, so a misspelt fix or option is refused rather than found mid-job.
Options typed on the command line are the job's defaults (`--trim`,
`--min-bleed`, `--bound`). A step's own options in the list come after them
and win. `preflight actions list | show | save | delete` manages them.

No lists are installed by default. Which chains suit the shop's work is the
owner's call.

## Spot colours (added 2026-11-18)

The Spot colours check lists every printing spot in use and what uses it,
because each prints on a plate of its own unless the RIP converts it. On the
samples: the catalogue has six spots in use on 13 pages (PANTONE 165, 283, 285
and 286 C, plus "Deep Blue" and "Silver") in what looks like a CMYK job. The
Uber Bag label has two Pantones, which look deliberate. The dieline, Skodix and
the banner's template layer are listed as non-printing. With `--cmyk-only`,
any printing spot is a fail.

`preflight fix rename-spot --spot "OLD=NEW"` (repeatable) renames a spot, or
merges it into an ink already in the file, taking that ink's definition. Only
the colour definitions change; content streams are not touched, because the
artwork refers to colours by a resource label. Verified: old names gone, new
names used everywhere the old were, and a plain rename renders identically.
On the catalogue: "Silver" -> "PANTONE 877 C", and "PANTONE 286 C" merged into
"PANTONE 285 C", gives 5 spots instead of 6.

`preflight fix spot-to-cmyk [--spot NAME ...]` (added 2026-11-18, approved as
proposed) converts spots to CMYK using each spot's own CMYK definition. A 60%
tint becomes 60% of that definition. Flat colour, strokes and text have their
colour instructions rewritten: the first fix to edit content streams, so the
rewritten page streams are new objects and the originals stay in the file,
unused. Placed graphics and tiling patterns are rewritten in place as
replacement objects. Spot gradients become CMYK gradients with the same stops.
Overprint settings are untouched.

A spot is converted only if every use of it can be: a plain CMYK definition,
and no use inside an image, an inline image, an annotation, or mixed with
other inks (DeviceN). Otherwise the whole spot is left alone, with the reason
and the pages. Non-printing spots, registration and process names are never
candidates. Spots defined in Lab or ICC are refused, because converting them
needs a colour profile.

Verified: the converted spots are no longer used anywhere, and every page that
used one renders the same as a composite CMYK proof before and after (pixels
within 3/255).

On the catalogue all six spots are now converted, including PANTONE 283 C and
285 C, which share a two-ink gradient on page 4 (added 2026-11-18). After
conversion the catalogue has no spot colours, and passes `--cmyk-only` on that
check.

How the rest was made convertible:

- `functions.py` evaluates PDF functions of every type: sampled, exponential,
  stitching, and the PostScript calculator that InDesign writes for two-ink
  colours. On the catalogue's own two-ink space it agrees with MuPDF to within
  0.36% ink, about one step of MuPDF's 8-bit output.
- A spot defined by any of those functions converts, not only the simple
  straight-line kind.
- A two-ink (DeviceN) colour converts when every ink in it is being converted
  or is a process ink. A mix with any other ink, such as a dieline, is still
  left alone.
- A gradient that cannot be folded in exactly becomes a sampled CMYK gradient
  at 256 steps, 16 bits each.

**Open question for the owner: two-ink gradients.** MuPDF does not follow the
PDF standard here, as this tool reads it. MuPDF blends between CMYK values
along a gradient. The standard, and how two inks overprint on press, blend the
inks' tints and convert each point through the inks' definition. For the
285 -> 283 gradient the midpoint differs by up to about 9% cyan (this tool:
about C56 M31; MuPDF's preview: about C65 M32). The conversion follows the
standard. There is no independent renderer on this machine to confirm it, so
it is worth checking once in Acrobat's Output Preview on the original
catalogue's page 4 gradient.

Because of that, converted gradients are verified on their own: at 101 points
each must equal the inks' definition applied to the original gradient's tints.
The page render comparison then checks everything else, against a reference
copy whose gradients carry this tool's conversion.

Also noted: the earlier page 4 result passed the render comparison only
because the gradient is a small part of the page, within the tolerance. That
check now no longer depends on MuPDF's gradient shortcut.

## The app: milestone 1, Inspect (added 2026-11-18)

`preflight app [file.pdf]` starts a small web server on this computer and
opens it in the browser. Built with Python's own web server and plain
HTML/CSS/JS, with no new dependencies and no internet use.

- **Security.** It answers only on 127.0.0.1. Every request needs the key the
  app printed. The Host header must name this machine, which guards against
  DNS rebinding. Requests whose Origin is another site are refused, so a
  website open in another tab cannot drive it. Files are read where they are;
  nothing is uploaded. The development launch config uses a fixed key and is
  kept out of git.
- **Read-only.** Milestone 1 has no way to write anything.
- **Screen.** Page thumbnails; the page rendered with colour management off;
  trim, bleed and page edge drawn as switchable lines, including a trim
  inferred from crop marks; the findings list, fails first. Clicking a finding
  jumps to its page and outlines its objects, padded so that a word or
  hairline still shows when the page is fitted. Zoom, arrow keys for pages,
  light and dark mode, and a narrow-window layout. "Open PDF" uses the Mac's
  own file chooser; a path can also be typed.
- **Engine.** `Result.geometry` gives the boxes each page was measured from,
  and the server converts every position to the rendered page with
  `device_matrix`.
- **Threads.** PyMuPDF is not safe to use from several threads, so the checks
  and all page renders take turns behind one lock. The page in view loads
  before the thumbnails.

Tried in the browser on the catalogue (56 pages: white overprint on page 48
outlined) and the 509 MB banner (the 200% black Multiply area outlined, and
the trim inferred from its crop marks).

## The app: milestone 2, Propose (added 2026-11-18)

Choose a fix, from the Propose tab or from the "Fix: …" button the app puts
on a finding it can address, set its options, and press Preview. The fix's
proposal runs and its result is written, verified as every fix is, to a
private temporary folder. Nothing goes to the user's folders, and the
preview is deleted when the next one starts, another file is opened, or the
app stops. The canvas then flips between Before and After, and the panel shows
what the fix would do, what it leaves alone and why, and the effect on
preflight (gone, changed, new).

Suggestions: Trim marks (inferred) -> trim-from-marks; Bleed artwork ->
add-bleed; Bleed box -> add-bleed or set-boxes; printing spots ->
spot-to-cmyk; one ink spelt two ways -> rename-spot; size not as expected ->
scale. The fix options in the app are the same as on the command line and go
through the same checks, so a bad value is refused with the reason.

Tried on the place cards: "Fix: add-bleed" with marks and clip gave a 114-page
preview showing the made bleed and marks, FAIL -> PASS.

## The app: milestone 3, Review and export (added 2026-11-18)

"Add to review" keeps a previewed fix. The review list is an action list: the
next preview is made on top of what is already kept, so each fix sees the file
as the steps before it left it. Review shows each step, with its options and
what it does, and the effect of all of them together. Removing a step runs the
rest again from the original. The canvas flips between the original and the
result.

Export: choose where to save (the Mac's save dialog, or a typed path), press
Export, then confirm in a dialog that names the file and says the original is
not changed. The server refuses an export without that confirmation, over an
existing file, or over the original. The result is checked once more (the
original's bytes intact at its start, structure sound) and copied to the new
file. "Show in Finder" reveals it.

Tried end to end in the browser on the place cards: add-bleed with marks,
kept, exported. The exported file starts with the original's bytes unchanged,
and preflight on it passes with "Trim marks agree with the trim box".

Bugs found while trying it, both fixed:

- The panels' layout style overrode the hidden attribute, so the Export panel
  and its confirm button showed on the Review screen. Nothing could have been
  written without a path, but a confirmation must never appear unasked.
  `[hidden]` now always wins.
- A deadlock: rebuilding the review list held the render lock and called a
  function that takes it again. The lock is now re-entrant, and the rebuild no
  longer takes it twice.

The four states from the interface model are now all in place. The owner finds
the screens "a little messy"; refining them is deferred (2026-11-18).

## Separations (added 2026-11-18)

`plates.py` renders each printing plate on its own through MuPDF's separations
interface, reached via PyMuPDF's low-level `mupdf` module, which PyMuPDF's
normal API does not expose. Pages render to the four process plates plus one
channel per spot ink, with colour management off. Overprint is honoured, but
only on that path: a page with no spots still goes through it with an empty
separations list, because otherwise MuPDF knocks out (black overprinting cyan
lost the cyan in testing).

In the app, the Plate picker in the toolbar lists the page's inks with the
share of the page each covers, marks spots and non-printing inks, and shows
any one plate, ink dark on white, for the original, a preview or the review
result.

On catalogue page 48 the Silver plate shows the white-overprint fault as it
would print: the silver bars run solid where the white words should be
knocked out.

Recent files (added 2026-11-18): the app remembers the last 12 PDFs opened
(paths only, in `~/.config/prepress-preflight/recent.json`, or the folder in
`PREFLIGHT_CONFIG`). They show on the start screen and in a Recent menu, with
moved or deleted files marked. Tests use a temporary settings folder so they
never touch the real list.

Native window (added 2026-11-18, PyWebView installed with the owner's
go-ahead): `preflight app` now opens its own window when PyWebView is present
(`pip install -e ".[app]"`), and `--browser` gives the browser tab as before.
The window shows the same pages from the same local server. Its addition is
drag and drop with real paths: PyWebView passes a dropped file's location to
Python (`pywebviewFullPath`), so the PDF is opened where it is, with no copy.
In a browser, a drop explains why it cannot open the file. Closing the window
stops the server and removes the temporary previews. Checked by the owner on
screen, including dragging a PDF onto the window (2026-11-18).

Next for the app: tidying the screens, when the owner has notes.

## Objects under a point (added 2026-11-18)

The first, read-only step of the object tree. In the app, clicking anywhere on
the page lists every object under that point, topmost first, in an "Objects
here" panel: what it is (filled shape, line, text, image, gradient), its
colour as an operator would write it (C94 M66 Y3 K64, PANTONE 186 C 50%,
Registration), total ink, line weight as printed, font, pixel size and
effective ppi, the forms it sits inside, and overprint or blend modes.
Hovering an entry outlines it on the page; Escape or × closes the panel.

It is built on the same content walker as the checks (`objects.py`), so what
it lists is exactly what the checks measured, with the same limits: an object
counts where it is drawn even where a clipping path hides it, and text
positions are approximate (one em per run). Clicks are turned back into PDF
coordinates with `device_matrix`, so rotated and UserUnit pages work.

Blend groups: a transparency group is blended onto the page as a whole, and
the objects inside start out Normal. So the walker now records each form's
group blend mode, and an object inside a Multiply group is shown as "in a
Multiply group". On the pull-up banner, one click on the black panel shows the
fault: K100 inside Fm0 (Multiply group), over a plain K100 box.

Object details (added 2026-11-18): selecting an object in the list shows
everything the file says about it:
  * size in mm, and position from the trim's top-left corner (the trim inferred
    from crop marks when the report uses one, and it says which);
  * how its colour is defined: the colour space resource, ICC profile name and
    channels, a spot's inks, alternate space and tint transform, and the CMYK
    the spot would print as if it were not separated (worked out with the
    tool's own function evaluator, at the object's tint);
  * text: font, type, whether it is embedded, size as printed (scaling
    included) and the characters shown, when the font is a simple one;
  * images: name, bits, compression, soft mask, interpolation;
  * graphics state: overprint fill and stroke, overprint mode, blend (and the
    group's blend when inside one), opacity, soft mask, and the ExtGStates in
    force;
  * the source: which stream (page or form chain), the instruction number, and
    the instructions that build and paint it.
The walker gets this from a "detail" mode: it walks the page exactly as for the
checks, and keeps a snapshot of the full state when it paints the chosen
object, so the details and the checks cannot disagree about which object it is.

## First object edit: overprint on or off (added 2026-11-18)

`object-overprint` sets overprint for one object, chosen in the inspector: the
object details have a "Knock out (overprint off)…" or "Set to overprint…"
button, which opens Propose with the fix filled in and previews it. From
there it is like any other fix: keep it in Review, export to a new file.

Chosen as the first edit because it fixes the catalogue's page 48 fault one
object at a time, and is the narrowest useful change. (Blend to Normal, the
banner's fix, stays parked as the owner asked.)

How it changes only that object: overprint is part of the graphics state,
which later objects inherit. So the fix adds two new ExtGStates and sets one
just before the object is painted (before the path is built, for a path,
since the state may not change mid-path; it may change inside a text block)
and the other just after, putting back exactly the overprint, both sides, and
overprint mode that were in force. Every other object on the page is then
verified unchanged, and the chosen one verified changed.

Refused, rather than changing more than asked:
  * an object inside a form painted more than once (every copy would change);
  * an object inside a pattern cell;
  * an object whose kind, colour and place no longer match what was chosen
    (`--expect`, filled in by the app): object numbers hold only for the file
    they came from. For the same reason, edits are offered only on the file the
    next fix starts from (the original, or the result of the fixes kept in
    Review), and object edits are not allowed in saved action lists.

On catalogue page 48 (checked 2026-11-18): white text "GREY" (object 1240)
overprints a Silver 100% box. After the edit the Silver plate under it has
the letter shapes knocked out (391 of 930 pixels in its box lose the silver,
none did before) and the White overprint finding goes from 14 to 13 text
objects. 14 of the page's 26 white overprinting objects are on the page; the
rest lie outside the media box, which the check rightly ignores.

## Knock out all white overprint (added 2026-11-19)

Owner's rule (2026-11-19): white overprint is never good and should always be
fixed. So `knock-out-white` fixes every object the White overprint check
reports, in one preview, and the app suggests it on that finding.

The single-object edit restores known overprint values after the object.
Across a whole file that is not safe: a form painted in two places can inherit
different settings at each. So here each white object is wrapped in
`q /GS gs … Q`, and `Q` puts back whatever was in force, wherever the form is
used. Two exceptions, where the state is put back with a second ExtGState and
so must be known (the same at every use): text, because `q`/`Q` are not
allowed in a text block, and a path that also sets a clip, because `Q` would
drop the clip.

Left alone and reported (they stay as fails): objects in pattern fills;
objects in a form that is also painted where it is not white (a form can
inherit its colour, and the change would reach the non-white uses); the
restore cases where uses disagree on overprint; a form without its own
resources painted more than once.

Verification compares every object on every page: the only allowed difference
is a white object going from overprint to knockout, and every target must have
changed.

On the catalogue (2026-11-19): 14 text objects on page 48 and 6 on page 49,
fixed in about 3 seconds; the White overprint check then passes. Every plate
of all 56 pages was compared before and after: pages 48 and 49 lose ink only
under the white text (Silver, CMYK), no page gains ink, and no other page
changes. (Page 5 showed a 3-level jitter in one run; the original rendered
against itself in the same way shows the same jitter, so it is MuPDF, not the
edit.)

The bracketing is now shared (`fixes/bracket.py`) so other fixes that change
some objects' state or colour use the same tested mechanism.

## Registration to black (added 2026-11-19)

`registration-to-black` is the fix for the Registration inside trim fail, and
the app suggests it there. Each registration object inside the trim becomes
black at the same tint (Registration 100% becomes K100, 50% becomes K50): my
call, as the usual pre-press convention. Marks outside the trim stay
registration. "Inside" is decided exactly as the check decides it, with the
check's own trim, so a trim inferred from crop marks is used when there is one.

Built on the shared bracketing; in a text block, black is set before each run
and the registration colour, as written, put back after. Left alone and
reported: one path that draws both marks outside the trim and artwork inside
it; registration mixed with other inks in one DeviceN colour; pattern fills;
a form also used outside the trim, or whose uses disagree on colour.
Verified: every target is CMYK 0/0/0/tint afterwards, and every other object
has its colour, place and overprint unchanged.

No sample has registration inside the trim (all 14 pass that check), so this
is tested on synthetic files only so far.

## Thicken hairlines (added 2026-11-19)

`thicken-hairlines` sets lines the Hairlines check reports to 0.25 pt as
printed (each at its own scale), and the app suggests it on that warning.
Same choice of lines as the check (white, dielines and anything outside the
print area left out). A form drawn at different sizes would need a different
width at each use, so it is left and reported.

Owner's decision (2026-11-19): only open lines. Outlines of closed shapes
(rectangles, paths closed with h/s/b, paths ending where they started), paths
mixing both, and outlined text are left and listed in the preview.

Results:
  * Wobbler: its one line (1.98 mm long, 0.23 pt) thickened; check passes.
  * Catalogue: 68 rules on pages 46-47 (3.3-10 mm long) thickened; check passes.
  * Little Stars: the "hairlines" are 0.03 pt strokes in an RGB illustration
    (a zebra on page 8, built from thousands of tiny shapes). Thickening all
    of them added grey rims and white notches to the stripes. Open lines only
    leaves 436 closed outlines, but 392 open strokes remain (0.02 mm to over
    10 mm long) and thickening them still shows grey rims. A minimum length
    does not separate them: at 3 mm, 58 lines remain and rims still show. No
    rule based on shape or length tells an illustrator's strokes from rules,
    so on this kind of file the preview is the judge. Owner's decision
    (2026-11-19): the preview decides. The fix stays suggested; when over a
    quarter of a page's lines are under 1 mm long, the proposal says so and
    asks for a Before/After look before keeping it (Little Stars page 8: 233
    of 360).

Speed: the bracketing now counts every form's uses in one walk and parses each
stream once (Little Stars proposal: 50 s, then 9.5 s, which is three walks of
about 3 s).

Next: other single-object edits (remove, recolour), when wanted.

## Preflight report to keep or send (added 2026-11-19)

`preflight report file.pdf [--out report.html]` and the app's "Save report…"
button write the findings as one self-contained HTML file: status, the
options checked against, every finding (fails first) with its pages and a
plain-English line on why it matters (not on notes, where it could mislead),
then pictures of the pages with something marked, the marks numbered to match
the findings, with trim and bleed drawn. Passed checks listed at the end.

Choices: thumbnails are JPEG (the 56-page catalogue report is 2.2 MB, the
banner's 216 KB; PNG made the catalogue's 7 MB), at most 24 pages pictured,
fails first. Large marked areas are outlined only: a tint over the banner's
black panel turned it maroon on the page. Pictures are drawn with colour
management off, as in the app, and the report says colours are approximate.
The report never overwrites a file (the PDF above all), must end in .html,
and defaults to "<name> preflight report.html" beside the PDF (numbered if
taken). Nothing is loaded from the internet, so it opens anywhere.

The command takes the same check options as `preflight` (now shared in
`cli_common.add_check_options`) and exits 1 on a FAIL, like `preflight`. The
app's button reuses the result of the check made when the file was opened, and
uses the Mac's save dialog (beside the PDF elsewhere); the save dialog itself
was not driven in testing (it is the system's own), the endpoint behind it is.

## RGB to CMYK (added 2026-11-19)

`rgb-to-cmyk` converts RGB colour through a CMYK profile; the app suggests it
on the RGB warning. Owner's defaults (2026-11-19): Coated FOGRA39 (Adobe's
CoatedFOGRA39.icc), `--profile` to override (fogra39, gracol2006, swop, or a
path); pure RGB black (0,0,0) text and vectors become K100 only.

My technical choices: relative colorimetric with black point compensation via
LittleCMS (Pillow); DeviceRGB taken as sRGB, embedded RGB profiles honoured,
CalRGB taken as sRGB. Vector and text colours rewritten in the content.
Images converted pixel by pixel and kept in their own compression: JPEG
photos as CMYK JPEG at the source's own quality (read from its quantisation
tables, 75-95), others Flate. Pillow writes CMYK JPEG inverted (Adobe's
convention), so those images carry /Decode [1 0 1 0 1 0 1 0]; checked to read
back right in MuPDF and macOS's renderer (without it, cyan read as its
opposite). Indexed images get a converted palette only. Axial and radial
gradients resampled at 256 steps. RGB blending groups switched to CMYK. Soft
masks and annotation appearances untouched. Left and listed: inline images,
images that are not 8-bit or carry Decode/colour-key masks, mesh and
function-based gradients, gradients written inside a resources dictionary.

Results (2026-11-19), all three RGB samples pass the RGB check afterwards:
  * Brochure cover: 1 photo. 1.6 MB -> 7.1 MB.
  * Little Stars: 49 pages, ~49,000 RGB objects. 48.5 -> 97.7 MB. Proposal
    8.5 s (34 s before a Pillow slowdown was avoided: applyTransform copies
    the profile into every result), write 24 s.
  * Yearbook: 68 pages, 84,000 RGB text objects, 365 images. 50 -> 172 MB.
  Colour-managed renders of before and after (MuPDF, ICC on) differ by a mean
  of 1/255 (yearbook), 5 (cover) and 7 (Little Stars): gamut mapping of
  bright RGB, and K100 black showing lighter than a rich black.

Size: CMYK JPEGs are about 2.5-3.5x an RGB JPEG of the same photo (four full
channels, no chroma subsampling), and the incremental update keeps the
original RGB images too. The compact export (below) drops those.

Near-black, owner's decision (2026-11-20): an option, generally off.
`--near-black-to-k` (a checkbox in the app) makes near-black text and vectors
K100 too. Near-black means neutral (channels within 2% of each other) with no
channel above 10%, so a navy-black stays coloured. Off, they follow the
profile, and the proposal counts them and names the option: Little Stars has
414 (about 318% ink each); 100 more dark colours there are not neutral.

## Reports for many files (added 2026-11-20)

`preflight report a.pdf b.pdf jobs/ [--out-dir FOLDER]` checks every PDF named
and every PDF directly inside the folders named (each once), writes each one's
report into one new folder, and an `index.html` summary: worst first, with
result, pages, trim size and the first three problems, each file linking to
its full report. A file that cannot be read is listed as "Could not check"
(exit code 2), never skipped. The default folder is "preflight reports <date
time>" inside the folder given, or beside the first PDF; a folder that is not
empty is refused. All 14 samples: 38 s, 8.6 MB of reports.

## Compact export (added 2026-11-20)

`--compact` on `preflight fix … --write`, and a Compact checkbox in the app's
Export, write the verified result as a fresh file holding only the objects
still in use, so what the fixes replaced is left out. Streams are copied byte
for byte (no decoding or re-encoding). My call: off by default, because the
normal export keeps the original's bytes intact at the start of the new file,
which is the strongest guarantee; the export says when the file grew by more
than a quarter and points at Compact.

Since the original's bytes are no longer at the start, a compact file is
checked against the verified full result instead: same page count and page
boxes, the same objects painted on every page (kind, colour, place, overprint,
blend, line weight, font, image size), every page rendered and compared (36 dpi,
CMYK, within 8 levels for MuPDF's run-to-run jitter), and no new structural
problems.

Yearbook after rgb-to-cmyk: 172 MB normal, 123 MB compact (the original is
50 MB), written and verified in 16 s. What remains is CMYK photos being larger
than RGB JPEGs.

## Image detail: enlarged images (added 2026-11-20)

The sample "BONUS - FAKE LOW RES … upscaled" passed every check (its pixel
count really is 300 ppi), but its photo was enlarged about 4.6x from a 66 ppi
original, so it prints soft. The new Image detail check (warn) finds that by
measurement, not AI vision: the sharpness of an image's strongest edges for its
resolution, the 99th percentile of |gradient| / local contrast (11 px window)
over pixels on real edges, sampled in a 3 x 3 grid of tiles. That is about
1 / edge width in pixels: an enlarged image spreads every edge wider.

Pixels are decoded by MuPDF, as they print. This was the hard part: Pillow's
raw reading of the sample's Adobe CMYK JPEG (APP14 transform 2) came back
inverted (K mean 235 of 255, against 20 as MuPDF decodes it and as the page
renders), which first hid the fault entirely. MuPDF applies both the JPEG's
Adobe transform and any /Decode array (as rgb-to-cmyk writes).

Calibration (samples, 2026-11-20): every one of the 199 genuine images the
samples paint that could be judged scored 0.436 or more. Genuine RGB and CMYK
photos enlarged by known factors (bicubic, then JPEG): 1.5x 0.37-0.47, 2x
0.29-0.38, 3x 0.25-0.31, 4x 0.18-0.26. Threshold 0.38 (about 2x or more);
real detail estimated as ppi / (0.47 / score)^2.

Results: the upscaled sample is flagged as "1 image (placed 9 times) … enlarged
5x, roughly 63 ppi of real detail, not 300" (truth: 4.6x, 66 ppi). The
catalogue gains one warning, page 7: an 844 x 612 photo at 273 ppi estimated at
91 ppi of real detail; I looked at it, and it is visibly mushy (smeared
lettering, blocky compression), a true positive. Nothing else in the samples is
flagged. A deliberately motion-blurred catalogue photo is not flagged (0.44):
it keeps crisp edges across the blur, so soft photography passes and missing
resolution does not.

Scope: only images at the minimum resolution or more (lower ones are Image
resolution's), at least 300,000 pixels, inside the printed area; each image
judged once however often it is placed. Too few edges to judge (a smooth sky,
a flat tint): no verdict. Unreadable: "could not check". Cost: the yearbook's
preflight 2.7 s to 4.9 s.

expected.json (owner's go-ahead, 2026-11-20): the upscaled sample now expects
status WARN and an Image detail warn, keeping its "no Image resolution warn"
rule. Backup: samples/expected.before-image-detail.json.

## Remove one object (added 2026-11-20)

`remove-object`, from the object details' Remove… button (or `--object
PAGE:NUMBER`): the second object edit. How each kind is removed so nothing
after it changes: a path's painting operator becomes `n` (the path is still
built, so a clip set with it holds; a path both filled and stroked loses only
the chosen side, B to S or f); a text run is shown in the invisible render
mode with the mode in force put back right after (deleting the Tj would move
every later run in the block); an image or gradient's Do/sh is removed.
Refused like object-overprint: forms painted more than once, pattern cells,
an object that no longer matches what was chosen, saved action lists.
Verified: the page paints exactly the same objects less the one removed.
Catalogue page 48, the white "GREY" run removed: 2533 objects to 2532,
verified.

## Recolour one object (added 2026-11-20)

`recolour-object`, from the object details' Recolour… button (or `--object
PAGE:NUMBER --cmyk C,M,Y,K`, percent): the third object edit. Colour is
graphics state that later objects inherit, so the object is wrapped in
`q <k or K> … Q`. In a text block (no q/Q allowed) or around a path that also
clips (Q would drop the clip), the new colour is set just before and the
colour in force is written back straight after, exactly as the file had it:
the same colour space resource and values (a spot stays that spot).

Only the chosen side changes (a path filled and stroked keeps its other
side). Overprint is kept, and the proposal says so when the object
overprints, because with CMYK a 0% ink then leaves what is beneath. Shapes,
text and stencil masks only: images and gradients carry their own colours.
Refused like the other object edits: forms painted more than once, pattern
cells, an object that no longer matches, saved action lists, or a colour it
already has. Verified: every object on the page is unchanged except the
chosen one, which is now that CMYK.

Catalogue page 48: a line (C50 M40 Y40 K4) and a white text run (object 6)
set to M100, both verified, about 2 s each. The render differs only inside
the text's own box (78 pixels at 100 dpi). The first text run tried was
refused, correctly: it is inside a form painted three times.

## Overprint black (added 2026-11-20)

`overprint-black` sets small 100% black text and thin black lines to
overprint. Why: black that knocks out leaves a gap in the colour beneath, and
a fraction of a millimetre of misregistration shows it as a halo along fine
type and rules. Overprinting is the usual pre-press rule for small black, and
InDesign's [Black] swatch does it by default.

Rules (my call, recorded here): C0 M0 Y0 K100, or a Separation named Black at
100%. Text up to 12 pt as placed (font size times the text and page scaling)
and stroked lines up to 2 pt; `--max-text` and `--max-line` change both. Large
black is left knocking out, because 100% K alone overprinting a photo lets the
photo show through. Rich black is not touched. The overprint mode is set to 1
as well: with mode 0 a CMYK object's 0% C, M and Y still knock out, so
overprint would do nothing. Black in DeviceGray is listed and left (RIPs
differ on whether grey overprints the CMY beneath); convert it first. Each
object is bracketed as in knock-out-white; a filled and stroked path where only
one side is small black is left if the other side already overprints. Allowed
in saved action lists. Verified against every object in the file: only the
targets' overprint changed, and each now overprints in mode 1 (a mode change
on an object that does not overprint has no effect and is allowed).

On the samples: Tempest label 6 objects, Wobblers 7, catalogue 14 (its other
1,622 small black objects already overprint), X-am posters 202. All written
and verified, each in under 3 s. The yearbook's text is RGB, so
`rgb-to-cmyk+overprint-black --compact` converts it first (526 pure blacks
become K100) and then overprints the small ones on seven pages: 21 s in all,
verified.

**The Black overprint check (added 2026-11-20)** notes the same objects, so
the app now suggests overprint-black. A note, not a warning: on white paper
knocking out does no harm, and some jobs want it, so it never changes a
file's status (all 14 sample statuses unchanged). On the samples: Tempest 6
lines, Wobblers 4 lines and 3 text, catalogue 5 lines on page 4 and 3 on
page 5, X-am posters 202 text. The catalogue showed the fix had counted 14:
the other 6 are copies off the page in artwork placed across a spread (the
same form drawn on pages 4 and 5). The fix still changes them, since they are
the same instruction as the visible copy and nothing printed changes, but now
lists them apart, so its counts match the check's.

## Small text in several inks, and rich black text to K (added 2026-11-23)

Each ink is its own plate, and plates land a fraction of a millimetre apart.
Fine type built from several inks blurs and grows coloured fringes; one ink
prints it sharp. The Small text inks check (my call on the rules, recorded
in the table above) warns on small rich black, which is meant to read as
black and is cleanest as K alone, and notes other small text in three or
more inks, which is often a deliberate brand build. Small is 8 pt as placed
(text events now carry their placed size); an ink counts at 10%. Invisible
OCR text is ignored. CMYK only.

Measured first on the samples: only the catalogue and the X-am posters have
small text in several inks. The catalogue has 525 runs of up to 8 pt in
C75 M68 Y67 K90 (300% ink on fine type: warn) and
about 400 in C100 M69 Y0 K11 and other 3-ink builds (notes). The X-am posters
have 30 in C57 M49 Y48 K15, a dark grey build (note). Both files already fail,
so no status changed.

`rich-black-text` makes the warned text C0 M0 Y0 K100 (`--max-text`, default
8). Only the colour changes; overprint stays (chain overprint-black after it
to overprint the new black). Bracketed like the other object fixes; in text
blocks the colour in force is written back exactly as the file had it,
spot or device colour. The colour restore is now shared with
registration-to-black, which also gains device-colour restores (it used to
refuse them). Verified against every object in the file. Catalogue: 525 text
objects on 2 pages, written and verified in 2.7 s; afterwards the warnings
are gone and the 9 notes remain. The app suggests the fix for the warning.

## CMYK profiles and untag-cmyk (added 2026-11-25)

CMYK tagged with an embedded ICC profile (ICCBased, four components) tells a
colour-managing RIP which press the numbers were made for; a RIP managing
colour converts them to its own press profile when the two differ, and a
K-only black can then print in four inks. The walker treated tagged CMYK as
plain CMYK, so nothing reported it. Colour spaces now carry their profile
(ignored when comparing, so every check still treats tagged CMYK as CMYK),
and a new note names each profile and what uses it. It is a note because
conversion depends on the RIP's settings.

On the samples only the X-am posters are tagged: all their artwork (4,138
vector objects, 435 image uses, 274 text objects) is in "U.S. Web Coated
(SWOP) v2", a US press profile, so a RIP targeting FOGRA39 would convert it.
The Wobblers hold one tagged space, used only as a DeviceN process
definition, not to paint: not reported, not changed.

The untag-cmyk fix replaces the tag with DeviceCMYK wherever a colour space is
named for painting: resource colour spaces (pages, forms, patterns, fonts,
annotation appearances), images, gradients and indexed palettes. It leaves
spot colours' alternates, DeviceN process definitions, transparency groups and
the output intent. Verified by rendering every page's separated plates before
and after: they must be identical. On the X-am posters: 143 colour spaces and
42 images untagged, plates identical, and the only change in the report is
that the note is gone.

Found on the way: the X-am posters already carry zlib warnings ("input stream
is complete") on some images. A fix that rewrites such an image's dictionary
copies its data byte for byte, so the same warning appears again at the new
offset, and the append verifier, which compared warnings with their offsets,
refused the write. Structure problems are now counted by message with the
offset ignored: a problem is new only if it occurs more often than in the
original. The original bytes and every rewritten stream's data are still
proved unchanged separately.

### Looked at, not built (2026-11-25)

- Small text in a near-black tint (K90-99, which screens into dots): no
  sample has any; the catalogue's small grey text is a deliberate K50.
- Type 3 fonts: Little Stars has 50 (Canva), but all 1,541 glyphs are vector
  outlines, not bitmaps; they print like any font.
- Thin filled shapes (outlined strokes) for Hairlines: few on the samples,
  some covered by artwork painted later (which the check cannot see), and 76
  of the catalogue's are zero-width, whose printing depends on the RIP's scan
  conversion. Too noisy to judge without knowing what is painted on top.
- Bleed that does not continue the artwork (a photo stopping at the trim with
  a flat block in the bleed, so a trimming drift shows a strip of the wrong
  colour). Measured on every edge that bleeds: the average colour 0.25-1 mm
  inside the trim against 0.5-2.5 mm out, in 1 mm steps along the edge. Taken
  straight out, slanted artwork crossing the trim looks discontinuous (a
  diagonal on catalogue page 2); allowing the bleed to match the inside up to
  5 mm to either side, as the bleed check's slant rule does, every outer edge
  on the samples continues. What remains is spine edges of spreads (catalogue
  pages 8/9, 40/41, 45, 50), whose bleed holds the facing page, and which
  `--bound` already leaves out. No real fault to calibrate against: not built.

App: a fix preview's "effect on preflight" now also lists the notes a fix is
meant to change (CMYK profiles, Black overprint, Annotations, Image
resolution, Layers), not only fails and warnings; untag-cmyk's preview on the
X-am posters showed "No change to fails or warnings" before and now shows the
CMYK profiles note gone. (2026-11-25)

## Total ink on the separated plates (added 2026-11-25)

Total ink rendered a plain CMYK picture, where overprint is not simulated:
black set to overprint C75 M68 Y67 really prints 310% but read as 100%, a pass
not earned (and the overprint-black fix makes such places on purpose). It now
renders the separated plates, as the Separations view and the reversed-text
check do, where MuPDF honours overprint, and total ink is the sum of every
printing plate: process and spot, the usual definition of total area
coverage. Non-printing objects are still taken out first (a dieline knocking
out the artwork beneath does not hide its ink: the Dielines check reports the
knockout).

Effect on the samples: only the bag label changed, its peak from 136% to
100%. It prints two spots that do not overlap; 136% was PANTONE 534 C's CMYK
equivalent, but a solid spot is one ink at 100%. No status changed.

## Text measured from glyph widths (added 2026-11-25)

A text object's position was a one-em box where its run started, and the text
position did not move on after a run, so the inspector could not find a run
by its later letters and the reversed-text check sampled only its first
letter. Runs are now measured from the font's advance widths (/Widths for
simple and Type 3 fonts, /W and /DW for Identity-H two-byte fonts), with
character and word spacing, horizontal scaling, rise and TJ adjustments, and
the next run starts where the last ended. The height is still a fixed share of
the size. Fonts without a widths table, and other two-byte encodings, keep the
old one-em box (and the clipping test keeps its one-em-per-byte reach).

Checked against MuPDF's own glyph positions: where a run ends agrees on all
8,018 runs on the yearbook's first 20 pages (worst 0.14 pt); on the place
cards the per-glyph advances are identical; elsewhere the only large
differences are rotated text, which the horizontal comparison cannot judge,
or a line's last glyph, where MuPDF gives a glyph box rather than an advance.

Effect on the samples (no status changed): on the catalogue, one more white
run counts as reversed (130), and one rich-black run on page 49 now falls
wholly outside its clip (77); deleting it from a copy changes no rendered
pixel. Elsewhere only highlight boxes changed (they now cover the run).

## Clipping applied (added 2026-11-25)

Until now an object counted where it was drawn, even when a clipping path
hid it. The content walker now tracks the clip (W / W* paths, and each form's
bounding box) as a box in page space. An object wholly outside it is marked
hidden: kept in the walker's output, so objects are numbered as before for
the inspector and the fixes, but not shown to the checks, the crop-mark
finder or the inspector's "objects under a point". A partly clipped object's
position is cut to the clip. Spot colours and Font embedding still see
hidden objects, since the RIP still receives their spot and font.

Text positions are a one-em box at the start of a run (no font metrics), so
for the hidden test a run is stretched along its baseline to one em per byte,
the most it could reach: a run starting outside a clip that may reach into it
is still judged.

Verified on the samples before relying on it: on catalogue pages 48, 4 and 20,
deleting every object the walker calls hidden (2,116 on page 48) from an
in-memory copy changes no rendered pixel.

Effect on the samples (no status changed):

- Catalogue: white overprint on page 48 is 7 text objects, not 14 (the other
  7 are clipped away; the fail stands on the visible ones); rich-black small
  text on pages 48 and 49 is 162 objects, not 245; small text in three inks
  on page 4 is 36, not 47; black overprint on page 5 is 2 lines, not 3;
  a hidden object under 100% opacity on page 3 is no longer listed.
- Little Stars: 49 fewer RGB vector objects (clipped-away illustration
  fragments).
- Elsewhere only highlight boxes changed: clipped images now outline the part
  that shows.

Fixes that pick objects the way a check does (rich-black-text,
overprint-black, thicken-hairlines, registration-to-black) now leave objects
clipped away on the page, listed as "clipped away, so it does not print", so
what they change matches what the check reported: on the catalogue,
rich-black-text changes 84 and 78 text objects on pages 48 and 49, as the check
counts, and leaves 266 and 97 hidden ones. Inside a form, a hidden use is
still changed with the form (refusing it would block the uses that show), and
the proposal says so in a separate sentence. knock-out-white, overprint-spot
and rgb-to-cmyk still change every matching object in the file, hidden or not
(whole-file, as agreed for white); their proposals now count the hidden ones
apart ("Also 7 object(s) clipped away, which do not print, changed too").
rich-black-text, overprint-black and knock-out-white were written on the
catalogue: each verified, and each one's check no longer reports it.

## Page count of bound jobs (added 2026-11-24)

With `--bound`, the Page size check also judges the page count. Every leaf
has two sides, so an odd count warns: a page is missing or extra, or a single
cover page is in the file. An even count that is not a multiple of 4 is a
note: saddle stitching folds sheets into fours and would add blank pages,
while perfect binding needs only an even count (the check cannot tell which
binding). It lives in Page size rather than a check of its own so that an
unbound job never shows a page-count "pass" that was not checked. The two
bound samples pass: the yearbook (68) and the catalogue (56). Files supplied
as spreads are counted as they are.

## Blank pages (added 2026-11-24)

A page where no printing plate carries ink inside the trim. In a booklet that
is often deliberate (an inside cover, a notes page), so it is a note; a file
where every page is blank is a warning, since something went wrong in the
export (artwork on a hidden layer, set to white or to a non-printing spot).
Crop marks and slugs outside the trim, white objects, invisible text and
dielines do not count. Pages are screened on their objects first; only a page
with no inked object is rendered (36 dpi, on the separated plates) to confirm,
so the check costs nothing measurable on the samples. A page that cannot be
rendered is "could not check".

On the samples only the yearbook has one: page 2, the inside front cover. It
holds two shapes, white or outside the trim, and renders as paper. No status
changed. Three older tests used a page holding only a white object; they now
also draw some ink, as the new check rightly warns about a file with nothing
printing.

## Reversed small text (added 2026-11-24)

White type is bare paper left in every plate behind it. Out of a background
of two or more inks, the gap has to line up on each plate, so misregistration
pushes one ink into the letters and fine strokes fill in; on one ink there is
nothing to misregister. The check measures the background on the separated
plates (so overprint and spot inks count as they print, non-printing spots
do not). Thresholds, chosen from the samples
(owner left the call to Claude): warn up to 7 pt as placed, note from 7 to
10 pt, an ink counting at 30% or more. Font weight is not judged.

Pages are rendered at 100 dpi only when they hold small white text: a 6 pt
text box is still about 9 pixels tall there, and 200 dpi gave the same
findings (one borderline note fewer at 100) at three times the cost. It adds
about 2.4 s to the 56-page catalogue.

On the samples only the catalogue has any: 15 runs at 6.2 and 6.5 pt out of
C100 M69 on pages 4 and 5 (the "Optimum" lines on the blue panels, checked
by eye: white sans on a two-ink blue) are a warning, and 129 runs at 7.4 to
10 pt (mostly white 8 pt footers out of C94 M66) are a note. The X-am
posters' small white text all sits on one ink or paper. No status changed
(the catalogue already fails). No fix: the remedies (larger or bolder type, a
one-ink background, or a stroke of the background colour) are design changes.

### Thin white lines, and inks that must be together (added 2026-11-24)

The check is now "Reversed text and lines": a white stroked line under
0.5 pt as placed, out of two or more inks, is a warning for the same reason
as small white text. Thin coloured lines stay with the Hairlines check, which
leaves white ones out. A line's box is widened by 1.5 pt each side so the
background around it is measured.

The first run showed the background measure was wrong. Taking each plate's
90th percentile separately over the box counted inks that sit side by side:
the zebra on Little Stars page 8 has black stripes beside a cyan-black sky,
and the box around a seam read as "C65 K100" although no point carried both.
The rule is now that two or more plates at 30% must be together at the same
point over at least 40% of the box, and the background reported is the median
of those points. White letters cover well under half their box, so text over
a solid two-ink panel still passes the 40%; the catalogue's findings are
unchanged.

Strokes that outline a white-filled shape are left out (the stroke's box
matches a white fill's within 0.6 pt, which is how Illustrator draws a
filled and stroked path). Such a stroke is the edge of a white area: filling
in trims the shape slightly and leaves no line to lose. This removed every
Wobblers hit (two white dots, 0.27 pt outline, on M100 Y63), the eye whites on
Little Stars page 30, and 61 zebra outlines on page 8. Small white *shapes*
are not judged.

Left on the samples: four loose 0.03 pt white lines on the Little Stars zebra
over the C65 K34 sky (page 8). They are real lines out of two inks, as thin as
the Canva hairlines that check already reports on this file, so they stay a
warning. No status changed.

## Remove private data (added 2026-11-24)

`remove-private-data` takes out page-piece data (PieceInfo) on pages, placed
forms and the catalogue: the second, complete copy of the artwork Illustrator
stores when "Preserve Illustrator Editing Capabilities" is on, and InDesign's
small marker. Nothing in it prints. After it, Illustrator opens the file as
plain PDF objects rather than the original artwork, which the proposal says.
The saving shows only with `--compact`, since a normal write appends to the
original bytes. Thumbnails and XMP metadata are not touched.

Verified: no page-piece data left, every changed object is the original minus
that one entry (a test refuses a write that drops one more key), and form
content is byte for byte the same.

On the samples (all written with `--compact`, every report identical before
and after, originals unchanged):
- Illustrator files: banner 509 to 266 MB (243 MB of private data in about
  3,700 chunks), bag label 0.9 to 0.1 MB, Tempest label 0.4 to under 0.05 MB,
  the upscaled sample 1.5 to 0.8 MB, Wobblers 1.2 to 0.8 MB.
- InDesign files: only a marker; the small savings (catalogue 51.2 to
  49.9 MB) come from compacting, not from this fix (compacting the catalogue
  alone gives 49.7 MB).
- `remove-private-data+downsample-images --max-ppi 200 --to-ppi 150` takes the
  banner from 509.2 to 77.9 MB in 17 s (written and verified).

## Downsample images (added 2026-11-24)

`downsample-images` brings images placed over `--max-ppi` (default 450, the
usual Acrobat/PitStop threshold) to `--to-ppi` (default 300). The resolution is
the effective one, and an image placed several times is judged by its largest
placement, so no use falls under the target. The Image resolution check now
notes images over 450 ppi, and the app offers the fix for that note.

Two things found on the samples and built in:
- **Transparency fringes.** Canva's clip art carries a soft mask, and the
  colour under its transparent part is black (the AFL logo in Little Stars:
  44% of it). A plain resample pulls that black into the visible edge. So an
  image with a soft mask is resampled weighted by its opacity, and the mask
  itself stays at full resolution so the outline stays sharp. A test proves a
  white edge stays white where a plain resample darkens it.
- **The render check compares 0.7 mm cells, not pixels.** MuPDF shrinks a
  fine image for the screen by skipping pixels, so thin lines alias
  differently from a filtered image: page 39 of Little Stars differed by up to
  155 levels on single pixels at 36 dpi, but by at most 31 on 0.7 mm cell
  averages at 144 dpi. The check therefore proves place, colour and tone, not
  sharpness; at 600 dpi the logo edge was compared by eye and is clean.

Results (all written with `--compact` and verified; originals unchanged):
- **Pull-up banner**, `--max-ppi 200 --to-ppi 150`: the photo goes from
  10156 × 11786 px (300 ppi) to 5078 × 5893 px; the file from 509 to 320 MB,
  in 19 s with 2.7 GB of memory at peak. The other ~242 MB is Illustrator's
  private editing data (about 3,700 chunks under the page's PieceInfo), which
  does not print; removing it would be a separate fix.
- **Yearbook**: 16 images at 478 to 935 ppi on pages 11, 16 and 62 to 65;
  50.0 to 45.7 MB.
- **Little Stars**: 243 Canva images over 450 ppi (up to 2600); 48.5 to
  33.6 MB.

## Overprint dielines and other spots (added 2026-11-24)

`overprint-spot` is the fix for the Dielines warning: a dieline that knocks out
cuts its shape out of the artwork beneath, and since the dieline plate is never
printed, the job can carry a white hairline right where the cutter runs.
By default it takes every dieline (by name, as the check does); `--spot NAME`
names other spots instead, such as a varnish, foil or Scodix plate, which
normally overprint for the same reason.

Built on bracket.py like overprint-black: each object wholly in the chosen
spots, above 0%, gets overprint set around it and nothing else changes
(verified object by object). The overprint mode is not touched; it only
matters for CMYK. A path filled in one colour and stroked in the dieline is
left and said so, since setting one side would reach the other.

On the samples it changes nothing, correctly: the Wobblers' dieline and the
business card's SKODIX plate (page 3) already overprint, and no other sample
has a dieline. So it is proven on synthetic files (strokes, fills, text
blocks, a refused write).

## Remove a layer (added 2026-11-23)

`remove-layer --layer NAME` (repeatable; default: every hidden layer) takes
what is on a layer off the page and removes the layer from the file. The
Layers check had no fix; the app now offers this one for both its findings.

Layer content cannot simply be cut out. In both layered samples (Illustrator)
a saved graphics state (q) opened on the first layer is closed on the second,
so the layers' sections are not self-contained. So the fix keeps every
instruction that sets state and takes only the painting out, as remove-object
does: paths end with `n` (a clip still applies), images, gradients and forms
go, text is shown invisibly with the render mode put back. The layer's
BDC/EMC markers go, and the layer leaves the layer list (the list goes with
the last one). Placed objects and annotations on the layer go too.

All or nothing: if any page cannot be done (the layer inside a placed form,
or mixed with other layers in an OCMD) nothing is changed, since a page left
holding the layer's content would print it once the layer is gone.

Verified by rendering each changed page against a scratch copy of the original
whose default layer settings switch the layer off. MuPDF's own `set_layer`
call was tried first and silently changed nothing on the samples, so it is
not used.

On the samples:
- **Pull-up banner, "Skoop Template":** 48 objects off. The layer holds the
  crop marks, and the banner's trim box (859.8 × 2114.8 mm) is wrong; only the
  marks give the real 845 × 2100 mm. Removing the layer alone therefore turns
  Bleed box and Bleed artwork into fails. The proposal now says so whenever
  the marks are on the layer and the trim box disagrees with them, and
  `trim-from-marks+remove-layer` gives the right trim with the template gone
  (the remaining fail is the known Multiply). Written and verified.
- **Tempest label, "Marks & Bleeds":** 2 objects off (its crop marks, which
  agree with the trim box, so no warning). Written and verified.

## Flatten or remove annotations (added 2026-11-23)

Two fixes for the Annotations warning, which until now had none. A RIP may
or may not print an annotation set to print, and a form field's look is drawn
by the viewer, so what the client saw is not guaranteed on press.

- **flatten-annotations** draws each annotation set to print into the page,
  using its stored appearance placed on its rectangle as the PDF standard
  describes (12.5.5), then takes it off the page. Form fields taken off every
  page leave the form; the form goes when it has none. Comment marks (sticky
  notes, highlights, strike-outs, carets, file attachments) are never
  flattened even when set to print: Acrobat sets sticky notes to print by
  default, and a note icon is never artwork. Left, and said so: no stored
  appearance; a form that asks the viewer to redraw its fields
  (NeedAppearances, so the stored look may be stale); annotations on a layer,
  hidden on screen but set to print, or kept upright on a rotated page.
  Verified by rendering the page with its annotations before and after; it
  must look the same (a deliberately misplaced drawing is refused in a test).
  The proposal names any lettering in unembedded fonts, since the Font
  embedding check will then report it (field appearances usually use
  Helvetica; Acrobat's own flatten does the same).
- **remove-annotations** takes every comment, stamp and form field off the
  page, with their pop-up notes. Links, trap networks and printer's marks stay,
  as in the check. The page content is not touched (verified).

Which to use is the operator's call per job: an "APPROVED" stamp or a client's
markup belongs off the page, a filled form field usually belongs in it. The app
offers both for the warning and remove-annotations for the note.

No sample carries an annotation other than one link, so this is proven on
synthetic files and on a proof made with PyMuPDF's own annotation tools
(stamp, text box, sticky note, filled text field, ticked box).

## Transparency (added 2026-11-23)

The Transparency check lists live transparency other than blend modes:
objects drawn under 100% opacity, under a soft mask, and images with their
own alpha channel. The RIP has to flatten these where they overlap artwork;
modern RIPs do it well, older ones crudely, and a job supplied as PDF/X-1a
may have none. A note only, since whether it matters depends on the printer.
Painted objects now carry their opacity and soft mask (and images whether
they have alpha), gradients included.

On the samples: 7 of 14 files have some. The yearbook's are black picture-frame
boxes at opacity 0 (placeholders from the layout tool; they print nothing),
counted apart as invisible objects. The catalogue has soft-masked images on
page 4, the Wobblers soft masks and alpha images, Little Stars alpha images
and invisible objects. No status changed.

## Watched folder (added 2026-11-23)

`preflight watch IN --out OUT [--fix @list] [--job NAME]` is the hot folder:
every PDF directly inside IN gets a preflight report in OUT/reports, and with
--fix each file the fixes change gets a verified fixed copy in OUT/fixed plus
a report on that copy. The PDFs in IN are only read, never moved or renamed.
A file is taken once it has not been written to for --settle seconds (5), so a
copy still arriving waits, and once only: OUT/.preflight-watch.json records
each by name, size and modification time, so a replaced file is taken again
(its new fixed copy gets the next free name). OUT/log.txt has a line per file;
a file that cannot be read or fixed is an ERROR line and the others carry on.
--once takes what is ready and stops (for a scheduled run). The job's settings
reach both the checks and the fixes.

Not done: gradients in reduce-ink. Measured first: no CMYK gradient in the
samples goes over 300% (the catalogue's peak at exactly 300%; the X-am
posters' gradients are not CMYK), so they stay listed, not changed.

On the samples folder with `--fix knock-out-white --once`: 14 files in 76 s,
14 reports, one fixed copy (the catalogue); the samples' checksums unchanged.

## Fixes on many files at once (added 2026-11-23)

`preflight fix NAME|@list a.pdf b.pdf folder/ --out-dir FOLDER` runs the same
fixes (or a saved action list) on every PDF named or directly inside the
folders named: the batch use of action lists in production. It proposes for
all first and prints one line each (which pages change, what is left, and the
preflight status before and after), then asks once (or `--yes`) and writes
"<name> fixed.pdf" into the new (or empty) folder for each file that changes,
each verified as usual. Files that do not change are not written. A file that
cannot be read or fixed is listed as ERROR and the others carry on (exit code
2). Without --out-dir nothing is written. One-object fixes are refused, as in
saved lists; --write is for one file. With one PDF everything is as before.

On the 14 samples, `knock-out-white+overprint-black`: 4 change (Tempest,
Wobblers, catalogue, X-am posters), 10 unchanged, 50 s, all written and
verified, originals untouched.

## Job settings (added 2026-11-23)

The values still waiting on the owner (press limits, the safe margin per
product, bleed) are per kind of job, so they now have somewhere to live:
`preflight jobs save coated -- --ink-limit 320 --safe-margin 3` keeps them
under a name, and `--job coated` uses them on preflight, preflight report,
preflight app and preflight fix. A job is a text file in
~/.config/prepress-preflight/jobs/ (or PREFLIGHT_JOBS) holding the options as
typed, checked when saved (a typo is refused, not saved). The job's options
go first, so anything typed as well wins. A fix takes only the options a fix
step knows (--trim, --min-bleed, --bound, --ink-limit), so
`preflight fix reduce-ink file.pdf --job coated` takes the press limit from
the job. The text output starts with "Job settings: NAME" and the HTML report
lists it.

Found on the way: the app had no check options at all (always the defaults);
`preflight app` now takes them, and `--job`. No jobs are made yet: they wait
for the owner's numbers.

**In the app (added 2026-11-23):** a Job menu in the header lists the saved
jobs ("Job: as started" goes back to the settings the app was opened with).
Choosing one checks the file again with its settings; its tooltip, and the
file name's, say what the file is checked for. Changing job is refused while
fixes are kept in Review, since the re-check would clear them. reduce-ink's
limit field is now empty by default and takes the job's limit.

Found and fixed on the way: fix previews in the app re-checked the result
with the fix's own options, not the app's check settings, so a preview could
show a finding "gone" only because it was judged at a different limit or
margin (possible since the app took check options). Previews and the Review
rebuild now check with the app's settings, with only what a fix's options
state explicitly (--trim, --min-bleed, --bound, --ink-limit) on top. A test
proves it: it fails against the old behaviour.

Tried in the app on the X-am posters with a test job (300%, 3 mm): the
menu re-checks at 300%, the Total ink fail suggests reduce-ink, and its
preview with the limit field empty brings everything to 300% and shows the
fail gone.

## Reduce total ink (added 2026-11-23)

`reduce-ink --ink-limit 300` brings colours over the press's total ink limit
down to it. The limit must be given (200 to 399): the owner does not have the
press limits yet, and the check's 400% default is no limit to reduce to.

How a colour over the limit comes down (colours within it are untouched, so
nothing else shifts): first grey is moved into black (equal parts of C, M and
Y out, the same amount of K in, until it fits or K is 100%: two parts of ink
removed for one, about as dark); then, if still over, C, M and Y are scaled
down together, which keeps the hue. C100 M100 Y100 K100 at 300% becomes
C67 M67 Y67 K100. A standard UCR/GCR approximation, chosen over a profile
round trip because a round trip would also move every colour within the
limit (K-only text would become four-colour).

What it changes: flat CMYK colours of shapes, lines, text and stencil masks
(bracketed per object, as the other object fixes); CMYK images, per pixel,
only the pixels over the limit (the rest keep their exact values), keeping
their compression; a JPEG is saved again at its own quality and brought 2%
under the limit so the recompression noise cannot lift it back over; an
indexed CMYK image has its palette reduced, exactly. Listed and left:
gradients, CMYK inline images, colour-keyed images, and images in spot,
DeviceN or Lab colour. Verified: every object unchanged except the targets,
whose colours are exactly the reduced ones; every replaced image decodes at
the same size with nothing over the limit. The app suggests it for a Total
ink fail.

X-am posters at 300%: 1,270 vector colours (1,160 of them 400% black) and
153 image placements (most of them indexed CMYK), 5.6 s; Total ink at 300%
then passes (peak 300%), and the page looks the same at preview size. Two
things found on the way: the first version skipped indexed images without
saying so (now handled, and any image it cannot change is listed), and
copying the indexed images' original compressed data carried over the
originals' damaged-stream warnings, which the structure check rightly
refused; the indexes are now decompressed and compressed afresh.

## Set the output intent (added 2026-11-20)

`set-output-intent` says which print condition the file's CMYK was made for,
for a RIP or a PDF/X workflow: an output intent (/S /GTS_PDFX, ICC registry)
with the profile embedded, by default Coated FOGRA39 (identifier FOGRA39);
gracol2006 (CGATS TR 006), swop (CGATS TR 001) or a custom .icc as for
rgb-to-cmyk. My calls: it does not claim PDF/X conformance (that needs far
more); a file that already states an output intent is left alone, and said so,
unless --replace; only the document catalogue changes. It is not suggested
automatically (the "no output intent" note is on almost every sample), but it
chains: `rgb-to-cmyk+set-output-intent`.

Brochure cover, `rgb-to-cmyk+set-output-intent --compact`: from WARN (RGB) to
PASS on every check, stating Coated FOGRA39, 6.0 MB. A4 PBR (already GRACoL
2013) is left alone.

## Speed: what was measured (2026-11-20)

Full preflight takes about 15 s on the banner and on the catalogue. Profiled:
on the banner half the time is the edge strips, rendered separately by the
bleed and safe-margin checks, each render decoding its 106-megapixel photo; on
the catalogue it is spread over Blend over same ink (4.9 s), Image detail
(4.7 s) and the edge strips (3 s).

Tried and rejected: one wide render per edge, cropped for each check. A crop is
not the same as a direct render: 87 of 296 strips across the samples differed,
by up to 38 levels (MuPDF's rendering depends on the clip it is given, likely
how it samples large images), so sharing renders would change bleed and margin
measurements. No change made. Any speed-up must keep each check's pixels
identical, or be recalibrated against the samples.

### Checks in parallel (2026-11-24)

The slow checks now run in their own processes on files of 8 MB or more:
the edge strips (Bleed box, Bleed artwork, Safe margin, kept together
because they share measurements), Blend over same ink, Total ink and Image
detail. The rest run in the main process meanwhile. Each process opens the
file itself and sees the same pages and pixels as before, so nothing a check
measures changes. Every sample's full report (findings, locations and page
boxes) was compared, one process against parallel: all 14 identical.

| Sample | Before | After |
| --- | --- | --- |
| Catalogue (56 pp) | 18.3 s | 5.2 s |
| Pull-up banner | 14.5 s | 7.4 s |
| Little Stars (49 pp) | 10.3 s | 7.0 s |
| Yearbook (68 pp) | 5.0 s | 3.3 s |

Small files stay in one process (starting workers costs more than it
saves). Peak memory on the banner is unchanged (about 5 GB in all, either
way): one check's render dominates. Each worker handles one task and exits,
so the app or a watched folder does not keep a big file's memory. If workers
cannot be started the run falls back to one process with the same report.
`PREFLIGHT_WORKERS=1` turns it off.

## Next tasks, in order

1. **Banner follow-ups.** The RIP that printed the banner is not known
   (owner, 2026-11-17), so what it did with the Multiply stays an inference.
   Watch whether the warn level is too noisy on InDesign jobs.
2. **Safe margin: confirm the distance.** 1.5 mm is the default; the owner may
   want a larger one per product type.
3. **Total ink: set the real press limit.** The area report, `--ink-limit` and
   the reduce-ink fix are done. The owner does not have the press limits yet (2026-11-17), so the
   default stays at 400% as a placeholder.
4. **Compare against Acrobat and PitStop** on real jobs; investigate every
   disagreement.
5. Later checks: none outstanding from the original list.
6. Later phases: a fix for the banner's Multiply fault (parked by the owner);
   checking the two-ink gradient midpoint in Acrobat; object editing.

## Interface model (for later)

One canvas, four states: inspect (read-only), propose (a fix shown as a
before/after preview), review (pending changes list), export (always a new
file). Checks and fixes are separate kinds of operation in the engine.
