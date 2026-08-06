from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from .model import Bar


REQUIRED_COLUMNS = {"timestamp", "open", "high", "low", "close", "volume"}
OPTIONAL_FLOAT_COLUMNS = (
    "trade_value",
    "depth_imbalance",
    "ofi_norm",
    "trade_delta_norm",
    "microprice_ticks",
    "l2_persistence",
    "spread_ticks",
    "l2_age_ms",
    "bid_wall_price",
    "bid_wall_ratio",
    "ask_wall_price",
    "ask_wall_ratio",
)


def _optional_float(row: dict[str, str], key: str) -> float | None:
    raw = row.get(key, "").strip()
    return None if raw == "" else float(raw)


def _parse_timestamp(raw: str, assumed_timezone: ZoneInfo) -> datetime:
    timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=assumed_timezone)
    return timestamp


def load_bars(path: str | Path, assumed_timezone: str = "UTC") -> list[Bar]:
    timezone = ZoneInfo(assumed_timezone)
    bars: list[Bar] = []
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("Input CSV is missing a header")
        missing = REQUIRED_COLUMNS - set(reader.fieldnames)
        if missing:
            raise ValueError(f"Input CSV is missing columns: {sorted(missing)}")
        for line_number, row in enumerate(reader, start=2):
            try:
                bar = Bar(
                    timestamp=_parse_timestamp(row["timestamp"], timezone),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    **{key: _optional_float(row, key) for key in OPTIONAL_FLOAT_COLUMNS},
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid data on CSV line {line_number}: {exc}") from exc
            if bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close):
                raise ValueError(f"Invalid OHLC relationship on CSV line {line_number}")
            if bar.volume < 0:
                raise ValueError(f"Negative volume on CSV line {line_number}")
            if bar.trade_value is not None and bar.trade_value < 0:
                raise ValueError(f"Negative trade_value on CSV line {line_number}")
            if bar.l2_age_ms is not None and bar.l2_age_ms < 0:
                raise ValueError(f"Negative l2_age_ms on CSV line {line_number}")
            for wall_key in ("bid_wall_price", "ask_wall_price"):
                wall_value = getattr(bar, wall_key)
                if wall_value is not None and wall_value <= 0:
                    raise ValueError(f"Non-positive {wall_key} on CSV line {line_number}")
            for wall_key in ("bid_wall_ratio", "ask_wall_ratio"):
                wall_value = getattr(bar, wall_key)
                if wall_value is not None and wall_value < 1.0:
                    raise ValueError(f"{wall_key} below 1.0 on CSV line {line_number}")
            bars.append(bar)

    bars.sort(key=lambda item: item.timestamp)
    for previous, current in zip(bars, bars[1:]):
        if current.timestamp <= previous.timestamp:
            raise ValueError("Timestamps must be unique and strictly increasing")
    return bars


def write_rows(path: str | Path, fieldnames: Iterable[str], rows: Iterable[dict[str, object]]) -> None:
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames))
        writer.writeheader()
        writer.writerows(rows)
