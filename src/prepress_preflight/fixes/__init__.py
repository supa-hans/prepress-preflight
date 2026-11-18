"""Fixes: proposed changes, kept apart from checks.

A fix never writes unless asked, and then only to a new file. Each one first
produces a Proposal saying exactly what would change, so the user can confirm
it, and the write is verified before the new file is put in place.
"""
from .base import Proposal
from .add_bleed import propose_add_bleed, propose_add_marks, write_add_bleed
from .scale import propose_scale, write_scale
from .set_boxes import propose_set_boxes, write_set_boxes
from .trim_from_marks import propose_trim_from_marks, write_trim_from_marks

FIXES = {
    "trim-from-marks": (propose_trim_from_marks, write_trim_from_marks),
    "set-boxes": (propose_set_boxes, write_set_boxes),
    "add-bleed": (propose_add_bleed, write_add_bleed),
    "add-marks": (propose_add_marks, write_add_bleed),
    "scale": (propose_scale, write_scale),
}

__all__ = ["FIXES", "Proposal"]
