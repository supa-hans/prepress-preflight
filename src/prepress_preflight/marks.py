"""Find crop marks in the page content and work out the trim they point at."""
from __future__ import annotations

from .model import Box

THIN_PT = 0.6  # a mark is a straight line: this thin across
MIN_LEN_PT, MAX_LEN_PT = 4.0, 90.0
REACH_PT = 60.0  # a mark ends within this distance of the corner it points at
SAME_PT = 0.3


def _lines(content):
    """Straight, axis-aligned strokes in registration colour: (horizontal, vertical).

    horizontal: (y, x0, x1)   vertical: (x, y0, y1)
    """
    horizontal, vertical = set(), set()
    for e in content.events:
        if e.paint != "stroke" or e.bbox is None or e.hidden or not e.cs.is_registration:
            continue
        if e.colour is not None and len(e.colour) and max(e.colour) <= 0.001:
            continue  # the white keyline drawn under each mark
        for x0, y0, x1, y1 in e.boxes():
            w, h = x1 - x0, y1 - y0
            if h <= THIN_PT and MIN_LEN_PT <= w <= MAX_LEN_PT:
                horizontal.add((round((y0 + y1) / 2, 2), round(x0, 2), round(x1, 2)))
            elif w <= THIN_PT and MIN_LEN_PT <= h <= MAX_LEN_PT:
                vertical.add((round((x0 + x1) / 2, 2), round(y0, 2), round(y1, 2)))
    return horizontal, vertical


def implied_trim(content):
    """The rectangle the crop marks point at, or None when there are no usable marks.

    A corner is where a horizontal and a vertical mark point at the same spot
    without touching it: each mark stops short of the corner and lies wholly to
    one side. Registration targets (two lines crossing) and fold marks do not
    form corners. Where bleed marks draw a second, larger rectangle, the inner
    one is the trim.
    """
    horizontal, vertical = _lines(content)
    corners = []
    for y, hx0, hx1 in horizontal:
        for x, vy0, vy1 in vertical:
            side_x = -1 if hx1 <= x - SAME_PT else (1 if hx0 >= x + SAME_PT else 0)
            side_y = -1 if vy1 <= y - SAME_PT else (1 if vy0 >= y + SAME_PT else 0)
            if not side_x or not side_y:
                continue
            gap_x = x - hx1 if side_x < 0 else hx0 - x
            gap_y = y - vy1 if side_y < 0 else vy0 - y
            if gap_x <= REACH_PT and gap_y <= REACH_PT:
                corners.append((side_x, side_y, x, y))
    box = {}
    for side_x, side_y in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        here = [(x, y) for sx, sy, x, y in corners if (sx, sy) == (side_x, side_y)]
        if not here:
            return None
        # innermost: left/bottom corners take the largest value, right/top the smallest
        box[(side_x, side_y)] = (max(p[0] for p in here) if side_x < 0 else min(p[0] for p in here),
                                 max(p[1] for p in here) if side_y < 0 else min(p[1] for p in here))
    x0 = max(box[(-1, -1)][0], box[(-1, 1)][0])
    x1 = min(box[(1, -1)][0], box[(1, 1)][0])
    y0 = max(box[(-1, -1)][1], box[(1, -1)][1])
    y1 = min(box[(-1, 1)][1], box[(1, 1)][1])
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def infer_trim(page, content, tolerance_mm) -> bool:
    """Take the trim from the crop marks when they disagree with the trim box.

    The trim box is what bleed, margins, ink and registration are measured
    from. When crop marks point at a different rectangle the marks are the
    better evidence of the finished size: a wrong box is usually just the page
    size left in by default. The file's own box is kept as page.declared_trim,
    and page.trim_inferred is set so the report can say what was done.
    Returns True when the trim was replaced.
    """
    box = implied_trim(content)
    if box is None:
        return False
    page.marks_trim = Box(*box)
    t, m = page.trim, page.media
    if max(abs(page.mm(a - b)) for a, b in zip(box, (t.x0, t.y0, t.x1, t.y1))) <= tolerance_mm:
        return False
    slack = 0.5
    if box[0] < m.x0 - slack or box[1] < m.y0 - slack or box[2] > m.x1 + slack or box[3] > m.y1 + slack:
        return False  # marks pointing outside the page cannot be the trim
    page.declared_trim, page.trim, page.trim_inferred = page.trim, Box(*box), True
    return True
