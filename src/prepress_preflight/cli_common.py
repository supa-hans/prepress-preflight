"""Helpers shared by the command lines."""
from __future__ import annotations

import argparse
import re


def parse_trim(text: str) -> tuple[float, float]:
    m = re.fullmatch(r"\s*([\d.]+)\s*[xX×]\s*([\d.]+)\s*(mm)?\s*", text)
    if not m:
        raise argparse.ArgumentTypeError("use WIDTHxHEIGHT in millimetres, for example 90x55")
    return float(m.group(1)), float(m.group(2))
