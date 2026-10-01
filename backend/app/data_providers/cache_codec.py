"""Turning cached provider results into bytes and back, without pickle.

The provider cache can now live in a file (see cache_store.py). A pickle read
from a file is code execution waiting for anyone who can write to that file, so
this module is an explicit, closed codec instead: JSON, with a few tagged
shapes for the things JSON lacks, compressed with zlib.

What it can carry is exactly what providers return today:

  * None, bool, int, float, str, list, dict, tuple, date, datetime, pd.Timestamp
  * pandas DataFrames (numeric, bool, string/object, and datetime columns,
    tz-aware ones included)
  * the dataclasses registered in DATACLASS_REGISTRY (every dataclass in
    data_providers/base.py is registered automatically; register_dataclass()
    adds more), nested inside lists and each other

Anything else raises CodecError on encode, and the caller simply doesn't persist
that value. Any failure on decode (a truncated blob, a version from another
build, a dataclass that no longer exists, a tampered file) also raises
CodecError, which the cache turns into a plain miss. Decoding never executes
anything: it only ever builds the types above, by name from the registry.
"""

from __future__ import annotations

import dataclasses
import json
import zlib
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

# Bump when the on-disk shape changes. A blob from another version is a miss,
# never a guess.
CODEC_VERSION = 1

# zlib level 1: bar data compresses ~4x either way and level 1 is several times
# faster than the default, which matters because every cache write encodes.
_ZLIB_LEVEL = 1

_TAG = "__t"

DATACLASS_REGISTRY: dict[str, type] = {}


class CodecError(Exception):
    """A value can't be encoded, or a stored blob can't be decoded."""


def register_dataclass(cls: type) -> type:
    """Allow `cls` to be stored and restored. Usable as a decorator."""
    if not dataclasses.is_dataclass(cls):
        raise TypeError(f"{cls!r} is not a dataclass")
    DATACLASS_REGISTRY[cls.__name__] = cls
    return cls


def _register_provider_dataclasses() -> None:
    from app.data_providers import base

    for value in vars(base).values():
        if isinstance(value, type) and dataclasses.is_dataclass(value) and value.__module__ == base.__name__:
            register_dataclass(value)


_register_provider_dataclasses()


# --------------------------------------------------------------------------
# encode
# --------------------------------------------------------------------------


def encode_value(value: object) -> bytes:
    """Value -> compressed JSON bytes. Raises CodecError for an unsupported value."""
    try:
        text = json.dumps(_to_json(value), separators=(",", ":"), allow_nan=False)
    except CodecError:
        raise
    except (TypeError, ValueError, OverflowError) as exc:
        raise CodecError(f"cannot encode {type(value).__name__}: {exc}") from exc
    return zlib.compress(text.encode("utf-8"), _ZLIB_LEVEL)


def _scalar(value: Any) -> Any:
    """A JSON-safe python scalar. NaN/inf become None: allow_nan=False keeps the
    stored text strictly valid JSON, and NaN vs missing is not a difference any
    provider result relies on."""
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    return value


def _to_json(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, np.generic):
        return _to_json(value.item())
    if isinstance(value, (int, float)):
        return _scalar(value)
    if isinstance(value, pd.Timestamp):
        if value is pd.NaT:
            raise CodecError("NaT is not supported as a standalone value")
        return {_TAG: "ts", "v": value.isoformat()}
    if isinstance(value, datetime):  # before date: datetime is a date subclass
        return {_TAG: "datetime", "v": value.isoformat()}
    if isinstance(value, date):
        return {_TAG: "date", "v": value.isoformat()}
    if isinstance(value, list):
        return [_to_json(v) for v in value]
    if isinstance(value, tuple):
        return {_TAG: "tuple", "v": [_to_json(v) for v in value]}
    if isinstance(value, dict):
        if all(isinstance(k, str) for k in value) and _TAG not in value:
            return {k: _to_json(v) for k, v in value.items()}
        return {_TAG: "dict", "v": [[_to_json(k), _to_json(v)] for k, v in value.items()]}
    if isinstance(value, pd.DataFrame):
        return _encode_frame(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        name = type(value).__name__
        if DATACLASS_REGISTRY.get(name) is not type(value):
            raise CodecError(f"dataclass {name} is not registered for caching")
        fields = {f.name: _to_json(getattr(value, f.name)) for f in dataclasses.fields(value) if f.init}
        return {_TAG: "dc", "n": name, "f": fields}
    raise CodecError(f"unsupported type for caching: {type(value).__name__}")


def _encode_frame(frame: pd.DataFrame) -> dict:
    if isinstance(frame.columns, pd.MultiIndex) or not all(isinstance(c, str) for c in frame.columns):
        raise CodecError("only DataFrames with plain string column names can be cached")
    if len(set(frame.columns)) != len(frame.columns):
        raise CodecError("DataFrame has duplicate column names")
    if isinstance(frame.index, pd.MultiIndex):
        raise CodecError("MultiIndex DataFrames can't be cached")

    if isinstance(frame.index, pd.RangeIndex):
        index: dict = {"range": [frame.index.start, frame.index.stop, frame.index.step]}
    else:
        index = {"column": _encode_series(frame.index.to_series().reset_index(drop=True))}
    index["name"] = frame.index.name if isinstance(frame.index.name, str) else None
    columns = [{"name": name, **_encode_series(frame[name].reset_index(drop=True))} for name in frame.columns]
    return {_TAG: "df", "index": index, "columns": columns, "rows": len(frame)}


def _encode_series(series: pd.Series) -> dict:
    dtype = series.dtype
    if isinstance(dtype, pd.DatetimeTZDtype) or pd.api.types.is_datetime64_dtype(dtype):
        tz = series.dt.tz
        unit = series.dt.unit
        naive = series.dt.tz_convert("UTC").dt.tz_localize(None) if tz is not None else series
        ints = naive.to_numpy().view("int64")  # NaT is the minimum int64: round-trips as NaT
        return {"kind": "dt", "unit": unit, "tz": str(tz) if tz is not None else None, "values": [int(i) for i in ints]}
    if pd.api.types.is_bool_dtype(dtype) or pd.api.types.is_numeric_dtype(dtype):
        if pd.api.types.is_complex_dtype(dtype):
            raise CodecError("complex columns can't be cached")
        values = [None if pd.isna(v) else _scalar(v) for v in series.astype(object)]
        return {"kind": "num", "dtype": str(dtype), "values": [None if v is None else v for v in values]}
    if pd.api.types.is_object_dtype(dtype) or pd.api.types.is_string_dtype(dtype):
        values = []
        for v in series.astype(object):
            if v is None or (not isinstance(v, str) and pd.isna(v)):
                values.append(None)
            elif isinstance(v, (str, bool, int, float, np.generic)):
                values.append(_scalar(v))
            else:
                raise CodecError(f"object column holds an unsupported {type(v).__name__}")
        return {"kind": "obj", "dtype": str(dtype), "values": values}
    raise CodecError(f"unsupported column dtype {dtype}")


# --------------------------------------------------------------------------
# decode
# --------------------------------------------------------------------------


def decode_value(blob: bytes) -> object:
    """Bytes from encode_value -> the value. Raises CodecError on ANY problem."""
    try:
        payload = json.loads(zlib.decompress(blob).decode("utf-8"))
        return _from_json(payload)
    except CodecError:
        raise
    except Exception as exc:  # corrupt, truncated, tampered, wrong version: all one outcome
        raise CodecError(f"cannot decode cached value: {exc}") from exc


def _from_json(node: Any) -> Any:
    if isinstance(node, list):
        return [_from_json(v) for v in node]
    if not isinstance(node, dict):
        return node
    tag = node.get(_TAG)
    if tag is None:
        return {k: _from_json(v) for k, v in node.items()}
    if tag == "date":
        return date.fromisoformat(node["v"])
    if tag == "datetime":
        return datetime.fromisoformat(node["v"])
    if tag == "ts":
        return pd.Timestamp(node["v"])
    if tag == "tuple":
        return tuple(_from_json(v) for v in node["v"])
    if tag == "dict":
        return {_hashable(_from_json(k)): _from_json(v) for k, v in node["v"]}
    if tag == "dc":
        cls = DATACLASS_REGISTRY.get(node["n"])
        if cls is None:
            raise CodecError(f"unknown cached dataclass {node['n']!r}")
        return cls(**{name: _from_json(v) for name, v in node["f"].items()})
    if tag == "df":
        return _decode_frame(node)
    raise CodecError(f"unknown tag {tag!r}")


def _hashable(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_hashable(v) for v in value)
    return value


def _decode_series(spec: dict, rows: int) -> pd.Series:
    kind = spec["kind"]
    values = spec["values"]
    if len(values) != rows:
        raise CodecError("column length does not match the frame")
    if kind == "dt":
        arr = np.array(values, dtype="int64").view(f"datetime64[{spec['unit']}]")
        series = pd.Series(arr)
        if spec["tz"] is not None:
            series = series.dt.tz_localize("UTC").dt.tz_convert(spec["tz"])
        return series
    if kind in ("num", "obj"):
        return pd.Series(values, dtype=spec["dtype"])
    raise CodecError(f"unknown column kind {kind!r}")


def _decode_frame(node: dict) -> pd.DataFrame:
    rows = int(node["rows"])
    data = {spec["name"]: _decode_series(spec, rows) for spec in node["columns"]}
    frame = pd.DataFrame(data) if data else pd.DataFrame(index=pd.RangeIndex(rows))
    index = node["index"]
    if "range" in index:
        start, stop, step = index["range"]
        frame.index = pd.RangeIndex(start, stop, step)
    else:
        frame.index = pd.Index(_decode_series(index["column"], rows))
    frame.index.name = index.get("name")
    return frame
