"""The watchlist you edit in Settings: layer precedence, persistence, a damaged
file, validation, sector lookups, immediate effect, and the endpoints."""

from __future__ import annotations

import json
import logging
import os

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_app_settings, get_data_provider
from app.api.routers import scanner as scanner_router
from app.config import AppSettings
from app.data_providers import universe, universe_store
from app.data_providers.base import AllProvidersFailedError, CompanyOverview, DataProviderError, QuoteData
from app.main import app
from app.services import watchlist_service
from app.services.watchlist_service import MAX_NAME_LOOKUPS_PER_SAVE, WatchlistError

CATALOGUE_CSV = """symbol,name,sector
AAPL,Apple Inc.,Technology
MSFT,Microsoft Corporation,Technology
JPM,JPMorgan Chase & Co.,Financials
XOM,Exxon Mobil Corporation,Energy
BTC-USD,Bitcoin,Crypto
NVDA,NVIDIA Corporation,Technology
"""
CATALOGUE = ["AAPL", "MSFT", "JPM", "XOM", "BTC-USD", "NVDA"]


@pytest.fixture(autouse=True)
def small_catalogue(tmp_path, monkeypatch):
    """A six-row bundled list, so none of these tests depends on the size of the real CSV."""
    path = tmp_path / "sp500.csv"
    path.write_text(CATALOGUE_CSV, encoding="utf-8")
    monkeypatch.setattr(universe, "UNIVERSE_FILE", path)
    universe.reset_universe_caches()
    yield
    universe.reset_universe_caches()


class FakeProvider:
    """Knows a handful of tickers; everything else has no quote."""

    def __init__(self, known: dict[str, str] | None = None, overview_fails: bool = False):
        self.known = known if known is not None else {"TSLA": "Tesla, Inc.", "SHOP.TO": "Shopify Inc.", "DOGE-USD": "Dogecoin"}
        self.overview_fails = overview_fails
        self.quote_calls: list[str] = []
        self.overview_calls: list[str] = []

    def get_quote(self, symbol: str) -> QuoteData:
        self.quote_calls.append(symbol)
        if symbol not in self.known and symbol not in CATALOGUE:
            raise AllProvidersFailedError(f"no quote for {symbol}")
        return QuoteData(symbol=symbol, price=10.0, change_pct_24h=0.0, volume=1.0, avg_volume_20d=1.0)

    def get_company_overview(self, symbol: str) -> CompanyOverview:
        self.overview_calls.append(symbol)
        if self.overview_fails or symbol not in self.known:
            raise DataProviderError("no overview")
        return CompanyOverview(symbol, self.known[symbol], None, None, None, None, None, None)


def _save(symbols, provider=None):
    watchlist_service.save_watchlist(symbols, provider or FakeProvider())


# --- precedence --------------------------------------------------------------


@pytest.mark.parametrize("dev", [None, "NVDA,TSLA"])
@pytest.mark.parametrize("custom", [None, ["JPM", "AAPL"]])
def test_precedence_matrix(monkeypatch, dev, custom):
    """Dev filter beats the saved list beats the bundled list."""
    if dev:
        monkeypatch.setenv("STRATEGEIA_DEV_TICKERS", dev)
    if custom:
        _save(custom)

    symbols = [e.symbol for e in universe.load_universe()]
    if dev:
        assert universe.active_layer() == "dev_filter"
        assert symbols == ["NVDA", "TSLA"]  # the env var's own order; a symbol outside the catalogue is kept
    elif custom:
        assert universe.active_layer() == "custom"
        assert symbols == ["JPM", "AAPL"]  # saved order, not catalogue order
    else:
        assert universe.active_layer() == "bundled"
        assert symbols == CATALOGUE


def test_dev_filter_is_normalised_and_deduplicated(monkeypatch):
    monkeypatch.setenv("STRATEGEIA_DEV_TICKERS", " nvda, AAPL ,nvda,, ")
    assert [e.symbol for e in universe.load_universe()] == ["NVDA", "AAPL"]


def test_blank_dev_filter_is_not_a_filter(monkeypatch):
    monkeypatch.setenv("STRATEGEIA_DEV_TICKERS", "  , ")
    assert universe.active_layer() == "bundled"
    assert len(universe.load_universe()) == len(CATALOGUE)


def test_dev_filter_ignores_but_does_not_delete_the_saved_list(monkeypatch):
    _save(["JPM", "XOM"])
    monkeypatch.setenv("STRATEGEIA_DEV_TICKERS", "AAPL")
    assert [e.symbol for e in universe.load_universe()] == ["AAPL"]
    monkeypatch.delenv("STRATEGEIA_DEV_TICKERS")
    assert [e.symbol for e in universe.load_universe()] == ["JPM", "XOM"]


def test_entries_carry_catalogue_names_and_sectors():
    _save(["NVDA", "TSLA"])
    nvda, tsla = universe.load_universe()
    assert (nvda.name, nvda.sector) == ("NVIDIA Corporation", "Technology")
    assert (tsla.name, tsla.sector) == ("Tesla, Inc.", universe.UNKNOWN_SECTOR)


def test_default_watchlist_takes_the_first_n_of_the_effective_list():
    _save(["XOM", "JPM", "AAPL"])
    assert universe.get_default_watchlist(2) == ["XOM", "JPM"]


def test_the_real_bundled_csv_still_loads(monkeypatch):
    monkeypatch.setattr(universe, "UNIVERSE_FILE", universe.DATA_DIR / "sp500.csv")
    universe.reset_universe_caches()
    entries = universe.load_universe()
    assert len(entries) >= 50
    assert next(e for e in entries if e.symbol == "AAPL").sector == "Technology"


# --- persistence -------------------------------------------------------------


def test_saved_list_survives_a_restart():
    _save(["TSLA", "AAPL"])
    path = universe_store.universe_file()
    assert path.exists() and path.parent.name == "watchlist_runtime"

    universe.reset_universe_caches()  # a new process: no in-memory state, same file
    assert [e.symbol for e in universe.load_universe()] == ["TSLA", "AAPL"]
    assert universe.active_layer() == "custom"
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["version"] == universe_store.FILE_FORMAT_VERSION
    assert [s["symbol"] for s in on_disk["symbols"]] == ["TSLA", "AAPL"]
    assert on_disk["updated_at"].endswith("Z")


def test_names_resolved_at_save_time_are_stored_and_not_looked_up_again():
    provider = FakeProvider()
    _save(["TSLA", "AAPL"], provider)
    assert provider.overview_calls == ["TSLA"]  # AAPL is in the catalogue: nothing to ask

    provider2 = FakeProvider()
    _save(["TSLA", "AAPL", "JPM"], provider2)
    assert provider2.overview_calls == []  # TSLA's name came from the saved file
    assert next(e for e in universe.load_universe() if e.symbol == "TSLA").name == "Tesla, Inc."


def test_a_failed_name_lookup_falls_back_to_the_symbol():
    _save(["TSLA"], FakeProvider(overview_fails=True))
    entry = universe.load_universe()[0]
    assert (entry.name, entry.sector) == ("TSLA", universe.UNKNOWN_SECTOR)


def test_a_provider_that_echoes_the_ticker_gives_no_name():
    _save(["TSLA"], FakeProvider(known={"TSLA": "tsla"}))
    assert universe.load_universe()[0].name == "TSLA"


def test_name_lookups_per_save_are_bounded():
    symbols = [f"ZZ{i}" for i in range(MAX_NAME_LOOKUPS_PER_SAVE + 5)]
    provider = FakeProvider(known={s: f"Name {s}" for s in symbols})
    _save(symbols, provider)
    assert len(provider.overview_calls) == MAX_NAME_LOOKUPS_PER_SAVE
    entries = universe.load_universe()
    assert entries[0].name == "Name ZZ0"
    assert entries[-1].name == entries[-1].symbol


def test_reset_removes_the_file_and_returns_to_the_bundled_list():
    _save(["TSLA"])
    assert watchlist_service.reset_watchlist() is True
    assert not universe_store.universe_file().exists()
    assert universe.active_layer() == "bundled"
    assert watchlist_service.reset_watchlist() is False  # nothing left to remove


def test_save_is_picked_up_without_clearing_anything():
    """No restart and no cache_clear between a save and the next read."""
    assert len(universe.load_universe()) == len(CATALOGUE)
    _save(["XOM"])
    assert [e.symbol for e in universe.load_universe()] == ["XOM"]
    _save(["XOM", "JPM"])
    assert universe.get_default_watchlist(50) == ["XOM", "JPM"]


def test_an_edit_made_outside_the_app_is_noticed():
    _save(["XOM"])
    assert universe.get_default_watchlist() == ["XOM"]
    path = universe_store.universe_file()
    path.write_text(json.dumps({"version": 1, "symbols": [{"symbol": "JPM"}, {"symbol": "AAPL"}]}), encoding="utf-8")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))  # force a new signature
    assert universe.get_default_watchlist() == ["JPM", "AAPL"]


def test_a_hand_written_file_may_list_plain_strings():
    path = universe_store.universe_file()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"symbols": ["aapl", "TSLA", "aapl"]}), encoding="utf-8")
    entries = universe.load_universe()
    assert [e.symbol for e in entries] == ["AAPL", "TSLA"]
    assert entries[0].name == "Apple Inc."


# --- damaged files -----------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        "{ this is not json",
        "[]",
        json.dumps({"symbols": "AAPL"}),
        json.dumps({"symbols": []}),
        json.dumps({"symbols": [{"symbol": "bad symbol!"}]}),
        json.dumps({"symbols": [{"name": "no symbol key"}]}),
        "",
    ],
)
def test_a_corrupt_file_falls_back_to_the_bundled_list(content, caplog):
    path = universe_store.universe_file()
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=universe_store.logger.name):
        assert [e.symbol for e in universe.load_universe()] == CATALOGUE
        assert universe.active_layer() == "bundled"
        assert universe.get_default_watchlist(2) == CATALOGUE[:2]
        assert universe.get_sector("AAPL") == "Technology"
    warnings = [r for r in caplog.records if "unusable" in r.getMessage()]
    assert len(warnings) == 1  # one warning, not one per call
    assert universe_store.read_store().error


def test_a_corrupt_file_is_reported_by_the_api_and_can_be_overwritten():
    path = universe_store.universe_file()
    path.parent.mkdir(parents=True)
    path.write_text("garbage", encoding="utf-8")
    client = _client()
    body = client.get("/api/watchlist").json()
    assert body["active_layer"] == "bundled"
    assert body["has_custom"] is False
    assert "could not be read" in body["custom_error"]

    resp = client.put("/api/watchlist", json={"symbols": ["AAPL"]})
    assert resp.status_code == 200
    assert resp.json()["active_layer"] == "custom"
    assert resp.json()["custom_error"] is None


def test_a_failed_save_leaves_the_previous_list_and_no_temp_file(monkeypatch):
    _save(["AAPL", "MSFT"])

    def boom(src, dst):
        raise OSError("disk full")

    with monkeypatch.context() as m:
        m.setattr(universe_store.os, "replace", boom)
        with pytest.raises(OSError):
            _save(["XOM"])
    path = universe_store.universe_file()
    assert [p.name for p in path.parent.iterdir()] == ["universe.json"]
    assert [e.symbol for e in universe.load_universe()] == ["AAPL", "MSFT"]


# --- validation --------------------------------------------------------------


def test_normalize_upper_cases_trims_and_deduplicates_keeping_first_order():
    assert watchlist_service.normalize_symbols([" aapl", "msft ", "AAPL", "btc-usd", "BRK.B", "^vix"]) == [
        "AAPL",
        "MSFT",
        "BTC-USD",
        "BRK.B",
        "^VIX",
    ]


@pytest.mark.parametrize("bad", ["", "  ", "TOOLONGSYMBOL1", "A B", "AA$", "ÄPPL", "a/b", "A,B"])
def test_normalize_rejects_malformed_symbols(bad):
    with pytest.raises(WatchlistError, match="Not a valid symbol"):
        watchlist_service.normalize_symbols(["AAPL", bad])


def test_normalize_names_every_bad_symbol():
    with pytest.raises(WatchlistError) as exc:
        watchlist_service.normalize_symbols(["A B", "AAPL", "C$"])
    assert "'A B'" in str(exc.value) and "'C$'" in str(exc.value)


def test_normalize_needs_at_least_one_symbol():
    with pytest.raises(WatchlistError, match="at least one"):
        watchlist_service.normalize_symbols([])
    with pytest.raises(WatchlistError, match="at least one"):
        watchlist_service.normalize_symbols(["aapl"] * 0)


def test_normalize_size_limit():
    limit = universe_store.MAX_WATCHLIST_SYMBOLS
    assert len(watchlist_service.normalize_symbols([f"S{i}" for i in range(limit)])) == limit
    with pytest.raises(WatchlistError, match=f"at most {limit}"):
        watchlist_service.normalize_symbols([f"S{i}" for i in range(limit + 1)])
    # duplicates don't count towards the limit
    assert len(watchlist_service.normalize_symbols(["AAPL"] * (limit + 50))) == 1


def test_the_size_limit_matches_the_scan_endpoints_cap():
    assert universe_store.MAX_WATCHLIST_SYMBOLS == scanner_router.MAX_SCAN_SYMBOLS


def test_a_rejected_save_writes_nothing():
    with pytest.raises(WatchlistError):
        _save(["AAPL", "no good"])
    assert not universe_store.universe_file().exists()


# --- sectors -----------------------------------------------------------------


def test_sector_resolves_for_a_symbol_removed_from_the_list():
    _save(["AAPL", "XOM"])
    _save(["AAPL"])  # XOM removed, but a position in it may still be open
    assert universe.get_sector("XOM") == "Energy"
    assert [e.symbol for e in universe.load_universe()] == ["AAPL"]


def test_sector_resolves_while_the_dev_filter_hides_the_symbol(monkeypatch):
    monkeypatch.setenv("STRATEGEIA_DEV_TICKERS", "AAPL")
    assert universe.get_sector("JPM") == "Financials"
    assert universe.get_sector("jpm") == "Financials"


def test_sector_is_none_for_a_symbol_with_no_sector():
    """The concentration rule treats None as "no opinion"; an unknown sector must
    never become one shared pseudo-sector that every added ticker falls into."""
    _save(["TSLA", "SHOP.TO"])
    assert universe.get_sector("TSLA") is None
    assert universe.get_sector("SHOP.TO") is None
    assert universe.get_sector("NOT-LISTED") is None
    entry = universe.load_universe()[0]
    assert entry.sector == universe.UNKNOWN_SECTOR  # what the UI is told, so it can say "unknown"


def test_an_added_crypto_pair_gets_the_crypto_sector():
    _save(["DOGE-USD"])
    assert universe.load_universe()[0].sector == "Crypto"
    assert universe.get_sector("DOGE-USD") == "Crypto"


def test_the_engines_sector_cap_treats_an_unknown_sector_as_no_opinion():
    """Two held symbols with no known sector must not count as "the same sector"."""
    from app.portfolio.engine import PaperTradingEngine
    from app.portfolio.models import PaperPosition

    _save(["TSLA", "SHOP.TO"])
    held = [
        PaperPosition(symbol="SHOP.TO", direction="long", entry_price=1, stop_loss=1, tp1=1, tp2=1, shares=1),
    ]
    engine = PaperTradingEngine(None, FakeProvider(), max_positions_per_sector=1)
    engine._check_sector_concentration("TSLA", held)  # no SectorConcentrationError


# --- validating a candidate symbol ------------------------------------------


def test_check_symbol_accepts_a_known_symbol_and_resolves_its_name():
    provider = FakeProvider()
    result = watchlist_service.check_symbol(" tsla ", provider)
    assert result.valid and result.symbol == "TSLA"
    assert result.name == "Tesla, Inc."
    assert result.sector == universe.UNKNOWN_SECTOR and result.sector_known is False
    assert result.in_catalogue is False
    assert provider.quote_calls == ["TSLA"]


def test_check_symbol_uses_the_catalogue_for_a_bundled_symbol():
    provider = FakeProvider()
    result = watchlist_service.check_symbol("AAPL", provider)
    assert (result.valid, result.in_catalogue, result.sector_known) == (True, True, True)
    assert result.name == "Apple Inc." and result.sector == "Technology"
    assert provider.overview_calls == []


def test_check_symbol_rejects_a_typo_with_a_clear_message():
    result = watchlist_service.check_symbol("TSLAA", FakeProvider())
    assert result.valid is False
    assert "No market data came back for TSLAA" in result.message


def test_check_symbol_rejects_a_malformed_symbol_without_asking_the_provider():
    provider = FakeProvider()
    result = watchlist_service.check_symbol("not a symbol", provider)
    assert result.valid is False and "Not a valid symbol" in result.message
    assert provider.quote_calls == []


def test_check_symbol_treats_a_zero_price_as_no_quote():
    class ZeroProvider(FakeProvider):
        def get_quote(self, symbol):
            return QuoteData(symbol=symbol, price=0.0, change_pct_24h=0, volume=0, avg_volume_20d=0)

    assert watchlist_service.check_symbol("TSLA", ZeroProvider()).valid is False


def test_check_symbol_survives_a_name_lookup_failure():
    result = watchlist_service.check_symbol("TSLA", FakeProvider(overview_fails=True))
    assert result.valid and result.name == "TSLA"


# --- endpoints ---------------------------------------------------------------


def _client(provider: FakeProvider | None = None, scan_size: int = 50) -> TestClient:
    app.dependency_overrides[get_data_provider] = lambda: provider or FakeProvider()
    app.dependency_overrides[get_app_settings] = lambda: AppSettings(scan_universe_size=scan_size)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    for dep in (get_data_provider, get_app_settings):
        app.dependency_overrides.pop(dep, None)


def test_get_describes_the_bundled_default():
    body = _client(scan_size=4).get("/api/watchlist").json()
    assert body["active_layer"] == "bundled"
    assert [e["symbol"] for e in body["entries"]] == CATALOGUE
    assert body["editable_entries"] == body["entries"]
    assert body["has_custom"] is False and body["custom_updated_at"] is None
    assert body["dev_filter"] is None
    assert body["bundled_size"] == len(CATALOGUE)
    assert (body["min_symbols"], body["max_symbols"]) == (1, 200)
    assert (body["scan_universe_size"], body["scanned_count"]) == (4, 4)


def test_put_saves_and_the_next_get_and_universe_agree():
    client = _client()
    resp = client.put("/api/watchlist", json={"symbols": ["tsla", "AAPL", "tsla"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["active_layer"] == "custom" and body["has_custom"] is True
    assert [e["symbol"] for e in body["entries"]] == ["TSLA", "AAPL"]
    assert body["entries"][0] == {"symbol": "TSLA", "name": "Tesla, Inc.", "sector": "Unknown", "sector_known": False}
    assert body["entries"][1]["sector_known"] is True
    assert body["custom_updated_at"].endswith("Z")
    assert client.get("/api/watchlist").json()["entries"] == body["entries"]
    # the existing endpoint the company dropdown uses returns the effective list, same shape as before
    assert client.get("/api/universe").json() == [
        {"symbol": "TSLA", "name": "Tesla, Inc.", "sector": "Unknown"},
        {"symbol": "AAPL", "name": "Apple Inc.", "sector": "Technology"},
    ]


def test_scanned_count_reports_the_first_n_of_m():
    client = _client(scan_size=2)
    body = client.put("/api/watchlist", json={"symbols": ["AAPL", "MSFT", "JPM", "XOM"]}).json()
    assert (body["scan_universe_size"], body["scanned_count"], len(body["entries"])) == (2, 2, 4)
    body = _client(scan_size=50).get("/api/watchlist").json()
    assert body["scanned_count"] == 4


@pytest.mark.parametrize(
    "payload",
    [{"symbols": []}, {"symbols": ["AAPL", "bad symbol"]}, {"symbols": [f"S{i}" for i in range(201)]}, {}],
)
def test_put_rejects_an_invalid_list_with_422_and_changes_nothing(payload):
    client = _client()
    resp = client.put("/api/watchlist", json=payload)
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], str)
    assert not universe_store.universe_file().exists()


def test_put_while_the_dev_filter_is_on_saves_but_does_not_change_what_runs(monkeypatch):
    monkeypatch.setenv("STRATEGEIA_DEV_TICKERS", "NVDA,AAPL")
    client = _client()
    body = client.put("/api/watchlist", json={"symbols": ["JPM", "XOM"]}).json()
    assert body["active_layer"] == "dev_filter"
    assert body["dev_filter"] == ["NVDA", "AAPL"]
    assert [e["symbol"] for e in body["entries"]] == ["NVDA", "AAPL"]
    assert [e["symbol"] for e in body["editable_entries"]] == ["JPM", "XOM"]
    assert body["has_custom"] is True
    assert [e["symbol"] for e in client.get("/api/universe").json()] == ["NVDA", "AAPL"]


def test_delete_resets_to_the_bundled_list():
    client = _client()
    client.put("/api/watchlist", json={"symbols": ["XOM"]})
    body = client.delete("/api/watchlist").json()
    assert body["active_layer"] == "bundled" and body["has_custom"] is False
    assert [e["symbol"] for e in body["entries"]] == CATALOGUE
    assert client.delete("/api/watchlist").status_code == 200  # idempotent


def test_validate_endpoint():
    client = _client()
    ok = client.post("/api/watchlist/validate", json={"symbol": "shop.to"}).json()
    assert ok["valid"] is True and ok["symbol"] == "SHOP.TO" and ok["name"] == "Shopify Inc."
    bad = client.post("/api/watchlist/validate", json={"symbol": "NOPE"})
    assert bad.status_code == 200
    assert bad.json()["valid"] is False and "NOPE" in bad.json()["message"]
    assert not universe_store.universe_file().exists()  # validating never saves


def test_the_watchlist_routes_require_auth(monkeypatch):
    from app.api import deps

    class Infra:
        allow_unauthenticated_api = False
        api_shared_secret = "s3cret"
        session_secret = "x"

    monkeypatch.setattr(deps, "get_infra_settings", lambda: Infra())
    client = _client()
    assert client.get("/api/watchlist").status_code == 401
    assert client.put("/api/watchlist", json={"symbols": ["AAPL"]}).status_code == 401
    assert client.post("/api/watchlist/validate", json={"symbol": "AAPL"}).status_code == 401
    assert client.delete("/api/watchlist").status_code == 401
    assert client.get("/api/watchlist", headers={"X-API-Key": "s3cret"}).status_code == 200
