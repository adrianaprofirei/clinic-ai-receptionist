"""'Câți pacienți pierzi lunar?' — the lost-revenue calculator.

    unanswered      = monthly inquiries × % unanswered or answered too late
    lost patients   = unanswered × % of answered inquiries that become patients
    monthly revenue = lost patients (rounded to 0.1) × average value per patient
    yearly revenue  = monthly revenue × 12

The page computes this live in JavaScript (static/js/calculator.js). This module
is the reference implementation: same operations, same rounding (half up, like
JS Math.round), checked against the JS code by tests/test_calculator.py.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass

LIMITS = {"inquiries": 100_000, "missed_pct": 100, "conversion_pct": 100, "value_ron": 1_000_000}
DEFAULTS = {"inquiries": 200, "missed_pct": 25, "conversion_pct": 30, "value_ron": 800}  # examples, not statistics

_LEADING_NUMBER = re.compile(r"^\s*[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?")


def parse_number(value: object, maximum: float) -> float:
    """Like the page: accept '12,5', clamp to [0, maximum], invalid -> 0."""
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        match = _LEADING_NUMBER.match(str(value or "").replace(",", ".", 1))
        number = float(match.group(0)) if match else math.nan
    if not math.isfinite(number):
        return 0.0
    return min(max(number, 0.0), float(maximum))


def round_half_up(x: float) -> int:
    """JavaScript's Math.round (Python's round() rounds half to even)."""
    floor = math.floor(x)
    return int(floor + 1 if x - floor >= 0.5 else floor)


def group(n: float) -> str:
    """12000 -> '12.000'."""
    return f"{round_half_up(n):,}".replace(",", ".")


def decimal(n: float) -> str:
    """15 -> '15', 12.25 -> '12,3' (one decimal, only when needed)."""
    tenths = round_half_up(n * 10)
    whole, rest = divmod(tenths, 10)
    return group(whole) + (f",{rest}" if rest else "")


@dataclass(frozen=True)
class LossResult:
    inquiries: float
    missed_pct: float
    conversion_pct: float
    value_ron: float
    unanswered: float
    lost_patients: float
    monthly_revenue: int
    yearly_revenue: int

    def formatted(self) -> dict[str, str]:
        return {
            "inquiries": group(self.inquiries),
            "missed_pct": decimal(self.missed_pct),
            "conversion_pct": decimal(self.conversion_pct),
            "value_ron": group(self.value_ron),
            "unanswered": decimal(self.unanswered),
            "lost_patients": decimal(self.lost_patients),
            "monthly_revenue": group(self.monthly_revenue),
            "yearly_revenue": group(self.yearly_revenue),
        }

    def explanation(self) -> list[str]:
        f = self.formatted()
        return [
            f"Cereri fără răspuns: {f['inquiries']} × {f['missed_pct']}% = {f['unanswered']}",
            f"Pacienți pierduți: {f['unanswered']} × {f['conversion_pct']}% = {f['lost_patients']}",
            f"Pe lună: {f['lost_patients']} × {f['value_ron']} RON = {f['monthly_revenue']} RON",
            f"Pe an: {f['monthly_revenue']} RON × 12 = {f['yearly_revenue']} RON",
        ]

    def as_dict(self) -> dict:
        return {**asdict(self), "formatted": self.formatted(), "explanation": self.explanation()}


def compute(inquiries: object, missed_pct: object, conversion_pct: object, value_ron: object) -> LossResult:
    inq = parse_number(inquiries, LIMITS["inquiries"])
    miss = parse_number(missed_pct, LIMITS["missed_pct"])
    conv = parse_number(conversion_pct, LIMITS["conversion_pct"])
    value = parse_number(value_ron, LIMITS["value_ron"])

    unanswered = inq * miss / 100
    lost = round_half_up(unanswered * conv / 100 * 10) / 10
    monthly = round_half_up(lost * value)
    return LossResult(inq, miss, conv, value, unanswered, lost, monthly, monthly * 12)
