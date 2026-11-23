"""Fixes: proposed changes, kept apart from checks.

A fix never writes unless asked, and then only to a new file. Each one first
produces a Proposal saying exactly what would change, so the user can confirm
it, and the write is verified before the new file is put in place.
"""
from .base import Proposal
from .add_bleed import propose_add_bleed, propose_add_marks, write_add_bleed
from .knock_out_white import propose_knock_out_white, write_knock_out_white
from .object_overprint import propose_object_overprint, write_object_overprint
from .registration_to_black import propose_registration_to_black, write_registration_to_black
from .overprint_black import propose_overprint_black, write_overprint_black
from .output_intent import propose_set_output_intent, write_set_output_intent
from .recolour_object import propose_recolour_object, write_recolour_object
from .reduce_ink import propose_reduce_ink, write_reduce_ink
from .remove_object import propose_remove_object, write_remove_object
from .rename_spot import propose_rename_spot, write_rename_spot
from .rich_black_text import propose_rich_black_text, write_rich_black_text
from .rgb_to_cmyk import propose_rgb_to_cmyk, write_rgb_to_cmyk
from .scale import propose_scale, write_scale
from .spot_to_cmyk import propose_spot_to_cmyk, write_spot_to_cmyk
from .set_boxes import propose_set_boxes, write_set_boxes
from .thicken_hairlines import propose_thicken_hairlines, write_thicken_hairlines
from .trim_from_marks import propose_trim_from_marks, write_trim_from_marks

FIXES = {
    "trim-from-marks": (propose_trim_from_marks, write_trim_from_marks),
    "set-boxes": (propose_set_boxes, write_set_boxes),
    "add-bleed": (propose_add_bleed, write_add_bleed),
    "add-marks": (propose_add_marks, write_add_bleed),
    "scale": (propose_scale, write_scale),
    "rename-spot": (propose_rename_spot, write_rename_spot),
    "spot-to-cmyk": (propose_spot_to_cmyk, write_spot_to_cmyk),
    "object-overprint": (propose_object_overprint, write_object_overprint),
    "knock-out-white": (propose_knock_out_white, write_knock_out_white),
    "registration-to-black": (propose_registration_to_black, write_registration_to_black),
    "thicken-hairlines": (propose_thicken_hairlines, write_thicken_hairlines),
    "rgb-to-cmyk": (propose_rgb_to_cmyk, write_rgb_to_cmyk),
    "set-output-intent": (propose_set_output_intent, write_set_output_intent),
    "reduce-ink": (propose_reduce_ink, write_reduce_ink),
    "rich-black-text": (propose_rich_black_text, write_rich_black_text),
    "overprint-black": (propose_overprint_black, write_overprint_black),
    "remove-object": (propose_remove_object, write_remove_object),
    "recolour-object": (propose_recolour_object, write_recolour_object),
}

# Fixes aimed at one object of one file: they make no sense in a saved action list.
ONE_FILE_ONLY = {"object-overprint", "remove-object", "recolour-object"}

__all__ = ["FIXES", "ONE_FILE_ONLY", "Proposal"]
