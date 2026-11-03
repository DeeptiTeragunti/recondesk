"""Exact money handling: signed decimal text to integer minor units (cents)."""
from __future__ import annotations

import re

_AMOUNT_PATTERN = re.compile(r"[+-]?\d+(?:\.\d{1,2})?", re.ASCII)


def parse_amount_minor(text: str | None) -> int:
    """Return a signed amount in cents. No floats, separators, exponents or NaN/Infinity.

    Raises ValueError for anything that is not a plain signed decimal with at most two places.
    """
    stripped = (text or "").strip()
    if not _AMOUNT_PATTERN.fullmatch(stripped):
        raise ValueError(f"invalid amount {text!r}: expected a signed decimal with at most 2 places")
    sign = -1 if stripped.startswith("-") else 1
    whole, _, fraction = stripped.lstrip("+-").partition(".")
    cents = int(whole) * 100 + int((fraction + "00")[:2])
    return sign * cents


def format_minor(value: int) -> str:
    """Render cents as a signed two-decimal string, e.g. -5 -> '-0.05'."""
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    return f"{sign}{magnitude // 100}.{magnitude % 100:02d}"


def normalize_reference(text: str | None) -> str:
    """Upper-case and collapse whitespace only. Digits and punctuation are kept as-is."""
    return " ".join((text or "").split()).upper()
