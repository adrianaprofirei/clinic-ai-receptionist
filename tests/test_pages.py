"""Landing and privacy pages: content, demo notice, security headers, assets."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.clinic import SERVICES

from .helpers import make_app


@pytest.fixture
def http(tmp_path):
    app, _clock = make_app(tmp_path)
    with TestClient(app) as client:
        yield client


def test_landing_page_renders_clinic_data_from_one_source(http):
    response = http.get("/")
    assert response.status_code == 200
    html = response.text
    for service in SERVICES:
        assert service.name["ro"] in html
    assert "1.200 <small>lei</small>" in html
    assert "Luni – Vineri</dt><dd>09:00 – 19:00" in html
    assert "Sâmbătă</dt><dd>09:00 – 13:00" in html
    assert "Duminică</dt><dd>Închis" in html
    assert "Câți pacienți pierzi lunar?" in html
    assert "Vrei asistentul ăsta pentru clinica ta?" in html
    assert 'href="mailto:contact@exemplu.ro' in html


def test_every_public_page_shows_the_demo_notice(http):
    for path in ("/", "/confidentialitate"):
        assert "Demo – clinică fictivă" in http.get(path).text


def test_calculator_is_server_rendered_with_example_values(http):
    html = http.get("/").text
    assert re.search(r'data-calc-out="lostPatients">15<', html)
    assert re.search(r'data-calc-out="monthlyRevenue">12.000<', html)
    assert "valori de exemplu" in html


def test_hero_example_talks_about_a_real_upcoming_day(http):
    html = http.get("/").text  # frozen clock: Wednesday 4 November 2026
    assert "Aveți loc mâine pentru un detartraj?" in html
    assert "Mâine, joi 5 noiembrie, am liber la:" in html


def test_security_headers_and_no_inline_scripts(http):
    response = http.get("/")
    csp = response.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "'unsafe-inline'" not in csp
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    scripts = re.findall(r"<script[^>]*>", response.text)
    assert scripts and all("src=" in tag for tag in scripts)
    assert not re.search(r'\sstyle="', response.text)  # CSP has no 'unsafe-inline' for styles either


def test_versioned_assets_are_cached_forever(http):
    html = http.get("/").text
    css = re.search(r'href="(/static/css/site\.css\?v=[0-9a-f]+)"', html).group(1)
    response = http.get(css)
    assert response.status_code == 200
    assert "immutable" in response.headers["cache-control"]


def test_privacy_page_explains_data_handling(http):
    html = http.get("/confidentialitate").text
    assert "24 de ore" in html and "OpenAI" in html and "zb_chat" in html


def test_healthz(http):
    assert http.get("/healthz").json() == {"status": "ok", "ai": False}
