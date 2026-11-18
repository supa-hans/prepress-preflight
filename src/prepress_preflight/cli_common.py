"""Helpers shared by the command lines."""
from __future__ import annotations

import argparse
import re


def parse_trim(text: str) -> tuple[float, float]:
    m = re.fullmatch(r"\s*([\d.]+)\s*[xX×]\s*([\d.]+)\s*(mm)?\s*", text)
    if not m:
        raise argparse.ArgumentTypeError("use WIDTHxHEIGHT in millimetres, for example 90x55")
    return float(m.group(1)), float(m.group(2))


def add_step_options(parser) -> None:
    """The options a fix step takes. Shared by `preflight fix` and saved action lists."""
    parser.add_argument("--trim", type=parse_trim, metavar="WxH", help="set-boxes, scale: the job's trim size in mm, e.g. 845x2100")
    shape = parser.add_mutually_exclusive_group()
    shape.add_argument("--fill", action="store_const", const="fill", dest="mode",
                       help="scale: when the shape differs by more than 1%%, scale to cover the trim and crop the overflow")
    shape.add_argument("--fit", action="store_const", const="fit", dest="mode",
                       help="scale: when the shape differs by more than 1%%, scale to fit inside the trim")
    parser.add_argument("--min-bleed", type=float, default=3.0, metavar="MM", help="bleed to give the new bleed box (default 3)")
    parser.add_argument("--method", choices=("mirror", "stretch"), default="mirror",
                        help="add-bleed: reflect the edge outward (default), or repeat its last row of pixels")
    parser.add_argument("--bound", action="store_true", help="bound job: spine edges need no bleed")
    parser.add_argument("--clip-to-bleed", action="store_true",
                        help="add-bleed, add-marks: clip everything on the page to the bleed box")
    parser.add_argument("--spot", action="append", metavar="SPOT",
                        help="rename-spot: OLD=NEW, rename a spot or merge it into one already in the file; "
                             "spot-to-cmyk: the spot to convert (default: all). Repeatable")
    parser.add_argument("--marks", action="store_true",
                        help="add-bleed: add trim marks outside the bleed box, replacing any existing ones")
