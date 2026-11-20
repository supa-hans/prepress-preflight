"""Helpers shared by the command lines."""
from __future__ import annotations

import argparse
import re


def parse_trim(text: str) -> tuple[float, float]:
    m = re.fullmatch(r"\s*([\d.]+)\s*[xX×]\s*([\d.]+)\s*(mm)?\s*", text)
    if not m:
        raise argparse.ArgumentTypeError("use WIDTHxHEIGHT in millimetres, for example 90x55")
    return float(m.group(1)), float(m.group(2))


def add_check_options(parser) -> None:
    """The options that change what the checks measure. Shared by `preflight` and `preflight report`."""
    parser.add_argument("--trim", type=parse_trim, metavar="WxH", help="expected trim size in mm, e.g. 90x55")
    parser.add_argument("--min-bleed", type=float, default=3.0, metavar="MM", help="minimum bleed (default 3)")
    parser.add_argument("--safe-margin", type=float, default=1.5, metavar="MM",
                        help="keep text and loose artwork this far inside the trim (default 1.5)")
    parser.add_argument("--ink-limit", type=float, default=400.0, metavar="PCT",
                        help="total ink limit in percent (default 400, a placeholder until the press limit is known)")
    parser.add_argument("--cmyk-only", action="store_true",
                        help="the job prints in process colours only: any printing spot colour is a fail")
    parser.add_argument("--bound", action="store_true",
                        help="bound job: spine edges need no bleed (page 1 is a right-hand page)")


def options_from(args):
    from .checks import Options
    return Options(expected_trim_mm=args.trim, min_bleed_mm=args.min_bleed, bound=args.bound,
                   ink_limit_pct=args.ink_limit, safe_margin_mm=args.safe_margin, cmyk_only=args.cmyk_only)


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
    parser.add_argument("--object", dest="target", metavar="PAGE:NUMBER",
                        help="object-overprint, remove-object, recolour-object: the object, as the app's inspector numbers it, e.g. 48:2439")
    parser.add_argument("--overprint", choices=("on", "off"), help="object-overprint: overprint on or off")
    parser.add_argument("--expect", metavar="TEXT", help="object-overprint, remove-object, recolour-object: refuse unless the object is still this one")
    parser.add_argument("--max-text", type=float, metavar="PT",
                        help="overprint-black: black text up to this size as placed is set to overprint (default 12)")
    parser.add_argument("--max-line", type=float, metavar="PT",
                        help="overprint-black: black lines up to this weight are set to overprint (default 2)")
    parser.add_argument("--cmyk", metavar="C,M,Y,K",
                        help="recolour-object: the new colour in percent, e.g. 0,0,0,100")
    parser.add_argument("--profile", metavar="NAME|PATH",
                        help="rgb-to-cmyk, set-output-intent: the CMYK profile: fogra39 (default), gracol2006, swop, "
                             "or the path to an .icc file")
    parser.add_argument("--near-black-to-k", action="store_true",
                        help="rgb-to-cmyk: near-black text and vectors (neutral, no channel above 10%%) become K100 "
                             "too; by default only pure black does")
    parser.add_argument("--replace", action="store_true",
                        help="set-output-intent: replace an output intent the file already states")
    parser.add_argument("--marks", action="store_true",
                        help="add-bleed: add trim marks outside the bleed box, replacing any existing ones")
