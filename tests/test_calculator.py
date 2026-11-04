"""The lost-revenue calculator: math, rounding, formatting, and JS/Python parity."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.calculator import DEFAULTS, compute, decimal, group, round_half_up

ROOT = Path(__file__).resolve().parents[1]
VECTORS = json.loads((ROOT / "tests" / "calculator_vectors.json").read_text())


def test_defaults_match_the_example_on_the_page():
    result = compute(**DEFAULTS)
    assert result.unanswered == 50  # 200 × 25%
    assert result.lost_patients == 15  # 50 × 30%
    assert result.monthly_revenue == 12_000  # 15 × 800 RON
    assert result.yearly_revenue == 144_000
    assert result.explanation() == [
        "Cereri fără răspuns: 200 × 25% = 50",
        "Pacienți pierduți: 50 × 30% = 15",
        "Pe lună: 15 × 800 RON = 12.000 RON",
        "Pe an: 12.000 RON × 12 = 144.000 RON",
    ]


def test_lost_patients_are_rounded_to_a_tenth_before_pricing():
    result = compute(137, 33, 27, 950)  # 45.21 unanswered -> 12.2067 lost
    assert result.lost_patients == 12.2
    assert result.monthly_revenue == 11_590  # 12.2 × 950, as the formula shown on the page
    assert result.formatted()["lost_patients"] == "12,2"


def test_rounding_is_half_up_like_javascript():
    assert round_half_up(12.5) == 13 and round(12.5) == 12  # Python's round would differ
    result = compute(10, 50, 25, 100)  # 1.25 lost patients
    assert result.lost_patients == 1.3 and result.monthly_revenue == 130


def test_inputs_are_clamped_and_invalid_values_count_as_zero():
    assert compute(200_000, 150, 30, 800).inquiries == 100_000
    assert compute(200, 150, 30, 800).missed_pct == 100
    assert compute(200, 25, -5, 800).lost_patients == 0
    assert compute("", "abc", None, "800 lei").monthly_revenue == 0
    assert compute("1500", "12,5", "40", "1200").yearly_revenue == 1_080_000


def test_romanian_number_formatting():
    assert group(1_080_000) == "1.080.000"
    assert decimal(15) == "15" and decimal(31.1) == "31,1" and decimal(0.04) == "0"


@pytest.mark.parametrize("case", VECTORS, ids=[str(i) for i in range(len(VECTORS))])
def test_python_matches_the_shared_vectors(case):
    i = case["input"]
    result = compute(i["inquiries"], i["missedPct"], i["conversionPct"], i["valueRon"])
    expected = case["expected"]
    assert result.unanswered == pytest.approx(expected["unanswered"])
    assert result.lost_patients == expected["lostPatients"]
    assert result.monthly_revenue == expected["monthlyRevenue"]
    assert result.yearly_revenue == expected["yearlyRevenue"]
    formatted = result.formatted()
    for js_key, py_key in (("lostPatients", "lost_patients"), ("monthlyRevenue", "monthly_revenue"),
                           ("yearlyRevenue", "yearly_revenue"), ("unanswered", "unanswered")):
        assert formatted[py_key] == expected["formatted"][js_key]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_javascript_still_matches_the_shared_vectors():
    script = """
      const c = require(process.argv[1]);
      const vectors = JSON.parse(require("fs").readFileSync(process.argv[2], "utf8"));
      const out = vectors.map(v => c.compute(v.input));
      process.stdout.write(JSON.stringify(out));
    """
    proc = subprocess.run(
        ["node", "-e", script, str(ROOT / "app/static/js/calculator.js"), str(ROOT / "tests/calculator_vectors.json")],
        capture_output=True, text=True, check=True, timeout=30,
    )
    for case, js in zip(VECTORS, json.loads(proc.stdout)):
        assert js["monthlyRevenue"] == case["expected"]["monthlyRevenue"]
        assert js["formatted"] == case["expected"]["formatted"]


def test_calculator_api(tmp_path):
    from fastapi.testclient import TestClient

    from .helpers import make_app

    app, _clock = make_app(tmp_path)
    with TestClient(app) as http:
        data = http.get("/api/calculator", params={"inquiries": 200, "missed": 25, "conversion": 30, "value": 800}).json()
    assert data["monthly_revenue"] == 12_000 and data["formatted"]["yearly_revenue"] == "144.000"
