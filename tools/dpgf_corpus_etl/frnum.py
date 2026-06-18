"""French number / € / % parsing, with Excel date-corruption detection.

Extends the spirit of ``lib/dpgf.py::_to_number`` ('1 247 000,00 €', '17,81%')
into a typed parser that returns a Decimal plus flags, never silently inventing a
value when a price cell was corrupted into a datetime by Google Sheets.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from .config import EMPTY_TOKENS, ERROR_TOKENS, MONEY_QUANT, normalize


@dataclass
class ParseResult:
    value: Decimal | None
    raw: str
    is_empty: bool = False          # '-', 'PM', blank → no value (≠ 0)
    is_error: bool = False          # '#DIV/0!' etc.
    date_corruption: bool = False   # a number/price came through as a datetime
    flags: list[str] = field(default_factory=list)


def _strip_number(s: str) -> str:
    """Remove currency, percent, and thousands separators; comma→dot decimal.

    Handles the French format ('1 234,56', NBSP/narrow-NBSP separators) and the
    US-format trap ('1,234.56') by treating the rightmost separator as decimal.
    """
    s = s.replace(" ", " ").replace(" ", " ").replace(" ", " ")
    s = s.replace("€", "").replace("%", "").strip()
    s = s.replace(" ", "")
    has_comma, has_dot = "," in s, "." in s
    if has_comma and has_dot:
        # rightmost separator is the decimal point
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif has_comma:
        s = s.replace(",", ".")
    return s


def parse_number(value: object, *, expect_money: bool = False) -> ParseResult:
    """Parse a cell value that may be a number, a French string, or corrupted.

    Returns ParseResult; callers decide what to do with empty/error/date flags.
    """
    raw = "" if value is None else str(value)

    # Already a Python number from openpyxl.
    if isinstance(value, bool):  # guard: bools are ints in python
        return ParseResult(None, raw, is_error=True, flags=["bool_cell"])
    if isinstance(value, (int, float)):
        d = Decimal(str(value))
        if expect_money:
            d = d.quantize(Decimal(MONEY_QUANT))
        return ParseResult(d, raw)

    # Date-corruption: a price/quantity cell that Excel coerced to a datetime.
    if isinstance(value, (_dt.datetime, _dt.date)):
        return ParseResult(
            None, raw, date_corruption=True,
            flags=["date_corruption"],
        )

    norm = normalize(value)
    if norm in EMPTY_TOKENS:
        return ParseResult(None, raw, is_empty=True, flags=["empty_token"])
    if norm in ERROR_TOKENS or (norm.startswith("#") and norm.endswith(("!", "?"))):
        return ParseResult(None, raw, is_error=True, flags=["formula_error"])

    cleaned = _strip_number(str(value))
    if cleaned in ("", "-", "."):
        return ParseResult(None, raw, is_empty=True, flags=["empty_after_strip"])
    try:
        d = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return ParseResult(None, raw, flags=["unparseable"])
    if expect_money:
        d = d.quantize(Decimal(MONEY_QUANT))
    return ParseResult(d, raw)


def parse_cost(value: object) -> ParseResult:
    """Parse a Fourniture/U cost cell (money, 2dp). Date-corrupted → needs_review."""
    return parse_number(value, expect_money=True)


def looks_like_date_serial(value: object) -> bool:
    """A bare number in the Excel-serial range for years ~2000–2030.

    Used to flag code/calibre columns silently coerced to serials.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return 36526 <= float(value) <= 47482
    return False
