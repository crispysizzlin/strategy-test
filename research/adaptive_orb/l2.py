from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median


RAW_FIELDS = {
    "received_utc",
    "depth_imbalance",
    "ofi_norm",
    "trade_delta_norm",
    "microprice_ticks",
    "signed_persistence",
    "spread_ticks",
}

# Written by newer recorder builds; passed through when present. Values may be blank
# on snapshots where no qualifying wall exists.
OPTIONAL_WALL_FIELDS = (
    "bid_wall_price",
    "bid_wall_ratio",
    "ask_wall_price",
    "ask_wall_ratio",
)


def aggregate_l2_csv(source: str | Path, destination: str | Path, tail_samples: int = 1) -> int:
    """Reduce the Quantower recorder's throttled snapshots to one close-of-minute row.

    Only the final ``tail_samples`` snapshots in each minute are used. This prevents the
    backtest from averaging information observed well before the minute-close decision.
    """

    groups: dict[datetime, list[tuple[datetime, dict[str, float]]]] = {}
    previous_timestamp: datetime | None = None
    with Path(source).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("L2 CSV is missing a header")
        missing = RAW_FIELDS - set(reader.fieldnames)
        if missing:
            raise ValueError(f"L2 CSV is missing columns: {sorted(missing)}")
        has_walls = all(field in reader.fieldnames for field in OPTIONAL_WALL_FIELDS)
        for line_number, row in enumerate(reader, start=2):
            try:
                timestamp = datetime.fromisoformat(row["received_utc"].replace("Z", "+00:00"))
                if timestamp.tzinfo is None:
                    raise ValueError("received_utc must include a UTC offset")
                if previous_timestamp is not None and timestamp <= previous_timestamp:
                    raise ValueError("received_utc must be unique and strictly increasing")
                previous_timestamp = timestamp
                minute = timestamp.replace(second=0, microsecond=0)
                values = {
                    "depth_imbalance": float(row["depth_imbalance"]),
                    "ofi_norm": float(row["ofi_norm"]),
                    "trade_delta_norm": float(row["trade_delta_norm"]),
                    "microprice_ticks": float(row["microprice_ticks"]),
                    "l2_persistence": float(row["signed_persistence"]),
                    "spread_ticks": float(row["spread_ticks"]),
                }
                walls: dict[str, float | None] = {}
                if has_walls:
                    for field in OPTIONAL_WALL_FIELDS:
                        raw = (row.get(field) or "").strip()
                        walls[field] = float(raw) if raw else None
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid L2 data on line {line_number}: {exc}") from exc
            groups.setdefault(minute, []).append((timestamp, values, walls))

    median_fields = [
        "depth_imbalance",
        "ofi_norm",
        "trade_delta_norm",
        "microprice_ticks",
        "l2_persistence",
        "spread_ticks",
    ]
    fieldnames = ["timestamp", *median_fields, "l2_age_ms"]
    if has_walls:
        fieldnames.extend(OPTIONAL_WALL_FIELDS)
    with Path(destination).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for minute in sorted(groups):
            tail = groups[minute][-max(1, tail_samples) :]
            latest_timestamp = tail[-1][0]
            l2_age_ms = max(
                0.0,
                ((minute + timedelta(minutes=1)) - latest_timestamp).total_seconds() * 1000.0,
            )
            output_row: dict[str, object] = {
                "timestamp": minute.isoformat(),
                **{field: median(row[field] for _, row, _ in tail) for field in median_fields},
                "l2_age_ms": l2_age_ms,
            }
            if has_walls:
                # Walls are point-in-time price levels; only the final observation of the
                # minute reflects the book the decision would have seen. Averaging prices
                # across snapshots with different walls would fabricate levels.
                latest_walls = tail[-1][2]
                for field in OPTIONAL_WALL_FIELDS:
                    value = latest_walls.get(field)
                    output_row[field] = "" if value is None else value
            writer.writerow(output_row)
    return len(groups)
