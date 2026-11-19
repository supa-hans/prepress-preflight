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
6. Later phases: a fix for the banner's Multiply fault (parked by the owner);
   checking the two-ink gradient midpoint in Acrobat; object editing.

## Interface model (for later)

One canvas, four states: inspect (read-only), propose (a fix shown as a
before/after preview), review (pending changes list), export (always a new
file). Checks and fixes are separate kinds of operation in the engine.
