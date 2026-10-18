"""Stored company logos (services/logo_service.py, api/routers/logos.py): only real, script-free images are kept,
unknown tickers are remembered, a rate limit stops a sync, and the endpoint serves the stored file safely."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import logo_service

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 2 2"><rect width="2" height="2"/></svg>'
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 40


class Resp:
    def __init__(self, status=200, content=SVG):
        self.status_code, self.content = status, content


@pytest.fixture(autouse=True)
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(logo_service, "logo_dir", lambda: tmp_path)
    monkeypatch.setattr(logo_service, "PAUSE_BETWEEN_DOWNLOADS", 0)
    return tmp_path


def serve(monkeypatch, responses):
    asked = []

    def fake_get(url, **kw):
        asked.append(url)
        return responses(url)

    monkeypatch.setattr(logo_service.httpx, "get", fake_get)
    return asked


def test_a_real_svg_is_stored_once(monkeypatch, folder):
    asked = serve(monkeypatch, lambda u: Resp())
    assert logo_service.fetch_logo("nvda") == "stored" and (folder / "NVDA.svg").read_bytes() == SVG
    assert logo_service.fetch_logo("NVDA") == "have" and len(asked) == 1
    assert asked[0].endswith("/NVDA")


def test_crypto_pairs_use_the_icon_set_and_a_png_is_kept(monkeypatch, folder):
    asked = serve(monkeypatch, lambda u: Resp(content=PNG))
    assert logo_service.fetch_logo("BTC-USD") == "stored" and (folder / "BTC-USD.png").exists()
    assert asked[0].endswith("/btc.png")


@pytest.mark.parametrize("kind", ["html", "script", "too big"])
def test_anything_but_a_clean_image_is_refused(monkeypatch, folder, kind):
    body = {"html": b"<html>not an image</html>", "script": b"<svg><script>alert(1)</script></svg>", "too big": SVG + b" " * 300_000}[kind]
    serve(monkeypatch, lambda u: Resp(content=body))
    assert logo_service.fetch_logo("AAA") == "error" and not list(folder.iterdir())


def test_an_unknown_ticker_is_remembered_not_asked_again(monkeypatch, folder):
    asked = serve(monkeypatch, lambda u: Resp(status=404))
    assert logo_service.fetch_logo("ZZZZ") == "missing"
    assert logo_service.fetch_logo("ZZZZ") == "skipped" and len(asked) == 1


def test_bad_symbols_never_reach_the_network(monkeypatch):
    asked = serve(monkeypatch, lambda u: Resp())
    assert logo_service.fetch_logo("../etc/passwd") == "error" and logo_service.fetch_logo("") == "error" and asked == []


def test_sync_stops_after_a_run_of_failures_and_skips_what_it_has(monkeypatch, folder):
    serve(monkeypatch, lambda u: Resp(status=429))
    counts = logo_service.sync_logos([f"S{i}" for i in range(20)])
    assert counts["error"] == logo_service.STOP_AFTER_FAILURES
    (folder / "HAVE.svg").write_bytes(SVG)
    serve(monkeypatch, lambda u: Resp())
    assert logo_service.sync_logos(["HAVE", "NEW"]) == {"stored": 1, "have": 1, "missing": 0, "skipped": 0, "error": 0}


def test_endpoint_serves_the_file_with_sandbox_headers_and_fetches_a_missing_one(monkeypatch, folder):
    serve(monkeypatch, lambda u: Resp())
    client = TestClient(app)
    r = client.get("/api/logos/AAPL")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml") and r.content == SVG
    assert "sandbox" in r.headers["content-security-policy"] and r.headers["x-content-type-options"] == "nosniff"
    serve(monkeypatch, lambda u: Resp(status=404))
    assert client.get("/api/logos/NOPE").status_code == 404
    assert client.get("/api/logos/..%2Fsecret").status_code == 404
