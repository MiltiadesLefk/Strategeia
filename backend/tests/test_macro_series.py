"""ECB and FRED parsers and providers on saved real excerpts. No network."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.data_providers import macro
from app.data_providers.base import DataProviderError
from app.data_providers.cache import clear_cache, configure_persistence
from app.data_providers.ecb_provider import EcbProvider, parse_ecb_csv, parse_period
from app.data_providers.fred_provider import FredProvider, parse_fred_csv
from app.main import app

client = TestClient(app)

# Real excerpt (2026-09), current header spelling, with the "." holiday marker.
FRED_CSV = """observation_date,DGS10
2026-09-21,4.96
2026-09-22,.
2026-09-23,5.11
2026-09-24,
2026-09-25,5.17
"""
# The older header spelling.
FRED_OLD_HEADER = "DATE,DGS10\n2024-01-02,3.95\n2024-01-03,3.91\n"

# Real excerpt of an ECB exchange-rate answer (columns trimmed, quoting kept:
# the TITLE_COMPL text contains commas inside quotes).
ECB_EXR = (
    "KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE,OBS_STATUS,TITLE_COMPL\n"
    'EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-09-30,1.1355,A,"ECB reference exchange rate, US dollar/Euro, 2.15 pm (C.E.T.)"\n'
    'EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-10-01,1.1298,A,"ECB reference exchange rate, US dollar/Euro, 2.15 pm (C.E.T.)"\n'
)
# A different dataflow layout (monthly, other column order, a missing value).
ECB_ICP = (
    "KEY,FREQ,TIME_PERIOD,OBS_VALUE,OBS_STATUS\n"
    "ICP.M.U2.N.000000.4.ANR,M,2025-10,2.1,E\n"
    "ICP.M.U2.N.000000.4.ANR,M,2025-11,,M\n"
    "ICP.M.U2.N.000000.4.ANR,M,2025-12,1.9,A\n"
)


@pytest.fixture(autouse=True)
def _fresh_cache():
    configure_persistence(None)
    clear_cache()
    yield
    clear_cache()


def test_fred_parser_skips_dot_and_blank_values():
    points = parse_fred_csv(FRED_CSV, "DGS10")
    assert points == [(date(2026, 9, 21), 4.96), (date(2026, 9, 23), 5.11), (date(2026, 9, 25), 5.17)]


def test_fred_parser_accepts_the_old_header():
    assert parse_fred_csv(FRED_OLD_HEADER, "DGS10")[0] == (date(2024, 1, 2), 3.95)


def test_fred_parser_rejects_an_html_page():
    with pytest.raises(DataProviderError):
        parse_fred_csv("<html><body>Rate limited</body></html>", "DGS10")


def test_fred_provider_builds_the_request_and_types_the_result():
    seen = {}

    def fake_get(url, params):
        seen.update(params)
        return 200, FRED_CSV

    series = FredProvider(fake_get).get_series("dgs10", date(2026, 9, 1), date(2026, 9, 30))
    assert seen == {"id": "DGS10", "cosd": "2026-09-01", "coed": "2026-09-30"}
    assert series.source == "fred" and series.series_id == "DGS10"
    assert series.last_observation == (date(2026, 9, 25), 5.17)
    assert series.last_updated is not None


@pytest.mark.parametrize("status", [404, 429, 500])
def test_fred_http_errors_are_clean(status):
    with pytest.raises(DataProviderError):
        FredProvider(lambda url, params: (status, "")).get_series("DGS10")


def test_fred_bad_id_and_empty_range_are_errors():
    provider = FredProvider(lambda url, params: (200, "observation_date,DGS10\n2026-09-22,.\n"))
    with pytest.raises(DataProviderError):
        provider.get_series("DGS10; rm -rf")
    with pytest.raises(DataProviderError):
        provider.get_series("DGS10")  # only a "." row: no observations, never an empty series


def test_ecb_parser_handles_quoted_commas():
    points = parse_ecb_csv(ECB_EXR, "EXR")
    assert points == [(date(2026, 9, 30), 1.1355), (date(2026, 10, 1), 1.1298)]


def test_ecb_parser_finds_columns_by_name_and_skips_blank_values():
    points = parse_ecb_csv(ECB_ICP, "ICP")
    assert points == [(date(2025, 10, 1), 2.1), (date(2025, 12, 1), 1.9)]


def test_ecb_parser_rejects_a_body_without_the_columns():
    with pytest.raises(DataProviderError):
        parse_ecb_csv("<html>error</html>", "x")
    with pytest.raises(DataProviderError):
        parse_ecb_csv("", "x")


def test_period_labels():
    assert parse_period("2026-10-01") == date(2026, 10, 1)
    assert parse_period("2026-09") == date(2026, 9, 1)
    assert parse_period("2026-Q3") == date(2026, 7, 1)
    assert parse_period("2026-W12") is None
    assert parse_period("") is None


def test_ecb_provider_request_shape_and_errors():
    seen = {}

    def fake_get(url, params):
        seen["url"], seen["params"] = url, dict(params)
        return 200, ECB_EXR

    series = EcbProvider(fake_get).get_series("EXR", "D.USD.EUR.SP00.A")
    assert seen["url"].endswith("/EXR/D.USD.EUR.SP00.A")
    assert seen["params"]["lastNObservations"] == "520" and "startPeriod" not in seen["params"]
    assert series.source == "ecb" and len(series.points) == 2

    EcbProvider(fake_get).get_series("EXR", "D.GBP.EUR.SP00.A", date(2026, 1, 1))
    assert seen["params"]["startPeriod"] == "2026-01-01"

    with pytest.raises(DataProviderError):
        EcbProvider(lambda u, p: (404, "")).get_series("EXR", "D.XXX.EUR.SP00.A")
    with pytest.raises(DataProviderError):
        EcbProvider(lambda u, p: (200, ECB_EXR)).get_series("EXR", "bad key!")


def test_results_are_cached_for_the_ttl():
    calls = []

    def fake_get(url, params):
        calls.append(1)
        return 200, FRED_CSV

    provider = FredProvider(fake_get)
    provider.get_series("DGS10")
    provider.get_series("DGS10")
    assert len(calls) == 1


def test_a_failed_refresh_serves_the_last_good_series():
    state = {"ok": True}

    def fake_get(url, params):
        return (200, FRED_CSV) if state["ok"] else (500, "")

    provider = FredProvider(fake_get)
    first = provider.get_series("DGS10", date(2026, 9, 1))
    from app.data_providers import cache as cache_module

    cache_module._store.clear()  # the fresh copy expired
    state["ok"] = False
    assert provider.get_series("DGS10", date(2026, 9, 1)).points == first.points


def test_get_macro_series_labels_from_the_catalogue():
    fred = FredProvider(lambda u, p: (200, FRED_CSV))
    series = macro.get_macro_series("dgs10", fred=fred)
    assert (series.series_id, series.name, series.unit, series.frequency) == ("DGS10", "US Treasury 10-year yield", "%", "daily")
    ecb = EcbProvider(lambda u, p: (200, ECB_EXR))
    eur = macro.get_macro_series("ECB_EURUSD", ecb=ecb)
    assert eur.unit == "USD per EUR" and eur.source == "ecb"
    clipped = macro.get_macro_series("ECB_EURUSD", end=date(2026, 9, 30), ecb=EcbProvider(lambda u, p: (200, ECB_EXR)))
    assert clipped.points == [(date(2026, 9, 30), 1.1355)]


def test_unknown_series_and_bad_range():
    with pytest.raises(macro.UnknownSeriesError):
        macro.get_macro_series("NOPE")
    with pytest.raises(DataProviderError):
        macro.get_macro_series("DGS10", date(2026, 2, 1), date(2026, 1, 1))


def test_every_catalogue_entry_is_well_formed():
    ids = [i.series_id for i in macro.list_series()]
    assert len(ids) == len(set(ids))
    for info in macro.list_series():
        assert info.source in ("fred", "ecb")
        if info.source == "ecb":
            assert info.ecb_flow and info.ecb_key
    for wanted in ("DGS2", "DGS10", "DGS3MO", "T10Y2Y", "DFF", "VIXCLS", "CPIAUCSL", "UNRATE", "DTWEXBGS"):
        assert wanted in ids


def test_macro_endpoints(monkeypatch):
    fred = FredProvider(lambda u, p: (200, FRED_CSV))
    monkeypatch.setattr(macro, "_fred", fred)
    listing = client.get("/api/macro/series")
    assert listing.status_code == 200 and any(row["series_id"] == "DGS10" for row in listing.json())

    ok = client.get("/api/macro/series/DGS10?start=2026-09-01")
    assert ok.status_code == 200
    body = ok.json()
    assert body["points"][-1] == {"date": "2026-09-25", "value": 5.17}
    assert body["last_observation_date"] == "2026-09-25"
    assert body["last_updated"].endswith("Z")

    assert client.get("/api/macro/series/NOPE").status_code == 404
    monkeypatch.setattr(macro, "_fred", FredProvider(lambda u, p: (500, "")))
    clear_cache()
    failed = client.get("/api/macro/series/DGS2")
    assert failed.status_code == 502
