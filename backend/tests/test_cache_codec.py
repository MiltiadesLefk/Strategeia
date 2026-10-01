"""The codec that lets cached provider results live in a file.

What matters: every value type a provider returns survives a round trip
unchanged (DataFrames with timezone-aware dates especially, since that is what
yfinance emits), a value it can't carry is refused rather than half-saved, and
reading a damaged or hostile blob is always a CodecError, never an exception of
some other kind and never code execution.
"""

from __future__ import annotations

import dataclasses
import json
import zlib
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from app.data_providers.base import (
    CompanyOverview,
    EarningsEstimate,
    EarningsHistoryEntry,
    FinancialsData,
    FinancialYear,
    InsiderActivity,
    NewsItem,
    OptionsSummary,
    QuoteData,
)
from app.data_providers.cache_codec import CodecError, decode_value, encode_value, register_dataclass


def roundtrip(value):
    return decode_value(encode_value(value))


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        7,
        1.5,
        "text",
        [1, "a", None, [2.5]],
        {"a": 1, "b": [1, 2]},
        date(2026, 9, 30),
        datetime(2026, 9, 30, 14, 5, 6),
        datetime(2026, 9, 30, 14, 5, 6, tzinfo=timezone.utc),
        (1, "x", date(2020, 1, 1)),
        {1: "int key", ("t", 2): date(2020, 2, 2)},
        {"__t": "looks like a tag but is user data"},
    ],
)
def test_plain_values_round_trip(value):
    assert roundtrip(value) == value


def test_nan_and_infinity_become_none_not_invalid_json():
    assert roundtrip([float("nan"), float("inf"), 1.0]) == [None, None, 1.0]


def test_numpy_scalars_are_stored_as_python_numbers():
    assert roundtrip([np.float64(1.5), np.int64(3), np.bool_(True)]) == [1.5, 3, True]


def test_timestamp_round_trips_with_timezone():
    ts = pd.Timestamp("2026-09-30 09:30:00", tz="America/New_York")
    back = roundtrip(ts)
    assert back == ts and back.tzinfo is not None


# Every dataclass a provider can return, with every optional field both set and None.
DATACLASS_SAMPLES = [
    QuoteData("AAPL", 189.5, -0.4, 5_000_000.0, 4_800_000.0),
    CompanyOverview("AAPL", "Apple", 3e12, 31.2, 4e11, 6.1, 160.0, 199.0),
    CompanyOverview("X", "X", None, None, None, None, None, None),
    FinancialsData("AAPL", [FinancialYear(2023, 3.8e11, 9.7e10), FinancialYear(2024, 3.9e11, 1.0e11)]),
    NewsItem("Headline", "Reuters", "https://example.com/a", "2026-09-30T12:00:00Z"),
    EarningsEstimate(date(2026, 10, 28), "Q4 2026", 1.4, None),
    EarningsHistoryEntry(date(2026, 7, 30), 1.3, 1.4, 7.7),
    OptionsSummary("AAPL", "2026-10-16", 0.8, None),
    InsiderActivity("AAPL", 90, 2, 5, 1.0e5, 9.0e5),
]


@pytest.mark.parametrize("sample", DATACLASS_SAMPLES, ids=lambda s: type(s).__name__)
def test_every_provider_dataclass_round_trips(sample):
    back = roundtrip(sample)
    assert back == sample and type(back) is type(sample)


def test_list_of_dataclasses_round_trips():
    items = [NewsItem(f"h{i}", "s", "u", "t") for i in range(3)]
    assert roundtrip(items) == items


def test_optional_none_result_round_trips():
    assert roundtrip(None) is None
    assert roundtrip(date(2026, 1, 2)) == date(2026, 1, 2)  # get_earnings_date


def test_dict_of_symbol_to_int_round_trips():
    ticker_map = {"AAPL": 320193, "MSFT": 789019}  # sec_edgar's ticker map
    assert roundtrip(ticker_map) == ticker_map


def _bars(tz):
    dates = pd.date_range("2026-01-02", periods=5, freq="B", tz=tz)
    return pd.DataFrame(
        {
            "date": dates,
            "open": [1.0, 2.0, np.nan, 4.0, 5.0],
            "high": [1.5, 2.5, 3.5, 4.5, 5.5],
            "volume": [10, 20, 30, 40, 50],
            "label": ["a", None, "c", "d", "e"],
            "flag": [True, False, True, True, False],
        }
    )


@pytest.mark.parametrize("tz", ["America/New_York", "UTC", None])
def test_dataframe_round_trips_with_and_without_timezone(tz):
    frame = _bars(tz)
    back = roundtrip(frame)
    pd.testing.assert_frame_equal(back, frame)
    assert str(back["date"].dtype) == str(frame["date"].dtype)  # tz and unit intact


def test_dataframe_keeps_nanosecond_and_second_units():
    for unit in ("s", "ms", "us", "ns"):
        frame = pd.DataFrame({"date": pd.Series(pd.date_range("2026-01-02", periods=3)).astype(f"datetime64[{unit}]")})
        back = roundtrip(frame)
        assert str(back["date"].dtype) == f"datetime64[{unit}]"


def test_dataframe_with_nat_round_trips():
    frame = pd.DataFrame({"date": pd.to_datetime(["2026-01-02", None, "2026-01-06"])})
    back = roundtrip(frame)
    assert back["date"].isna().tolist() == [False, True, False]


def test_dataframe_non_default_index_and_empty_frames():
    indexed = _bars("UTC").set_index("date")
    pd.testing.assert_frame_equal(roundtrip(indexed), indexed)
    empty = pd.DataFrame({"a": pd.Series([], dtype="float64"), "b": pd.Series([], dtype="int64")})
    pd.testing.assert_frame_equal(roundtrip(empty), empty)


def test_a_cached_frame_can_not_be_changed_through_the_stored_bytes():
    """encode takes a snapshot: mutating the frame afterwards doesn't alter what was stored."""
    frame = _bars(None)
    blob = encode_value(frame)
    frame.loc[0, "high"] = 999.0
    assert decode_value(blob).loc[0, "high"] == 1.5


@pytest.mark.parametrize(
    "value",
    [{1, 2}, object(), lambda: 1, b"bytes", pd.DataFrame({"a": [1j]}), pd.DataFrame({"a": [[1, 2]]})],
    ids=["set", "object", "function", "bytes", "complex", "list-cells"],
)
def test_unsupported_values_are_refused(value):
    with pytest.raises(CodecError):
        encode_value(value)


def test_unregistered_dataclass_is_refused_until_registered():
    @dataclasses.dataclass
    class Secret:
        x: int

    with pytest.raises(CodecError):
        encode_value(Secret(1))
    register_dataclass(Secret)
    assert roundtrip(Secret(1)) == Secret(1)


def test_frame_with_odd_columns_is_refused():
    with pytest.raises(CodecError):
        encode_value(pd.DataFrame({1: [1, 2]}))
    with pytest.raises(CodecError):
        encode_value(pd.DataFrame([[1, 2]], columns=["a", "a"]))


def _blob(payload) -> bytes:
    return zlib.compress(json.dumps(payload).encode())


@pytest.mark.parametrize(
    "blob",
    [
        b"",
        b"not zlib at all",
        zlib.compress(b"not json"),
        encode_value(_bars("UTC"))[:-10],  # truncated
        _blob({"__t": "mystery"}),
        _blob({"__t": "dc", "n": "NoSuchClass", "f": {}}),
        _blob({"__t": "dc", "n": "os.system", "f": {"command": "echo pwned"}}),
        _blob({"__t": "dc", "n": "QuoteData", "f": {"unexpected": 1}}),
        _blob({"__t": "date", "v": "not-a-date"}),
        _blob({"__t": "df", "rows": 2, "index": {"range": [0, 2, 1]}, "columns": [{"name": "a", "kind": "num", "dtype": "float64", "values": [1.0]}]}),
        _blob({"__t": "df", "rows": 1, "index": {"range": [0, 1, 1]}, "columns": [{"name": "a", "kind": "pickle", "values": [1]}]}),
    ],
)
def test_damaged_or_hostile_blobs_raise_codecerror_and_nothing_else(blob):
    with pytest.raises(CodecError):
        decode_value(blob)
