"""Fixes: proposed changes, kept apart from checks.

A fix never writes unless asked, and then only to a new file. Each one first
produces a Proposal saying exactly what would change, so the user can confirm
it, and the write is verified before the new file is put in place.
"""
from .base import Proposal
from .trim_from_marks import propose_trim_from_marks, write_trim_from_marks

FIXES = {"trim-from-marks": (propose_trim_from_marks, write_trim_from_marks)}

__all__ = ["FIXES", "Proposal"]
