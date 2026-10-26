"""Bundle one operating day of parsed snapshots into per-journey delay trajectories.

Output has one row per stop of a journey, with the final arrival/departure time and
where it came from (``*_source``):

- ``actual``         an actual time was observed
- ``last_expected``  no actual time; Entur's last prediction before the stop was passed
- ``cancelled``      the stop was cancelled
- ``missing``        nothing known

Use only ``actual`` rows as training labels; ``last_expected`` is a fallback for analysis.

Run from the repo root (needs parsed files for --date and the day after, for trips past midnight):
    uv run journeys --date 2026-10-26 [--dataset OST]
"""

import argparse
import logging
import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

log = logging.getLogger("journeys")

KEY = ["data_frame_ref", "dated_vehicle_journey_ref", "order"]
GAP_WARNING = pd.Timedelta(minutes=3)
MIN_ACTUAL_SHARE = 0.8


def load_parsed(parsed_dir: Path, dataset: str, day: date) -> pd.DataFrame:
    # Trips of operating day D can run past UTC midnight, so also read D+1.
    frames = []
    for d in (day, day + timedelta(days=1)):
        path = parsed_dir / "et" / dataset / f"date={d.isoformat()}.parquet"
        if path.exists():
            frames.append(pd.read_parquet(path))
        else:
            log.warning("missing parsed file %s", path)
    if not frames:
        raise FileNotFoundError(f"no parsed files for {day} in {parsed_dir}")
    df = pd.concat(frames, ignore_index=True)
    return df[df["data_frame_ref"] == day.isoformat()].sort_values("fetched_at")


def resolve(
    aimed: pd.Series, actual: pd.Series, expected: pd.Series, cancelled: pd.Series
) -> tuple[pd.Series, pd.Series]:
    """Pick the best known time per stop, and label where it came from."""
    time = actual.fillna(expected)
    source = pd.Series("missing", index=actual.index)
    source[expected.notna()] = "last_expected"
    source[actual.notna()] = "actual"
    source[cancelled] = "cancelled"
    time[cancelled] = pd.NaT
    # No aimed time means the event doesn't exist by design (e.g. no departure at the last stop).
    source[aimed.isna()] = "not_applicable"
    return time, source


def build_journeys(df: pd.DataFrame) -> pd.DataFrame:
    grouped = df.groupby(KEY, sort=True)
    # .last() takes the most recent non-null value per column (rows are sorted by fetched_at).
    stops = grouped[
        ["stop_point_ref", "aimed_arrival", "aimed_departure", "actual_arrival",
         "actual_departure", "call_cancelled", "journey_cancelled"]
    ].last()
    stops["first_seen"] = grouped["fetched_at"].min()
    stops["last_seen"] = grouped["fetched_at"].max()
    stops["n_snapshots"] = grouped.size()

    # Entur's last prediction while the stop was still ahead of the vehicle.
    estimated = df[df["call_type"] == "estimated"].groupby(KEY)
    last_expected = estimated[["expected_arrival", "expected_departure"]].last()
    last_expected["last_expected_made_at"] = estimated["fetched_at"].max()
    stops = stops.join(last_expected)

    cancelled = stops["call_cancelled"] | stops["journey_cancelled"]
    for kind in ("arrival", "departure"):
        time, source = resolve(
            stops[f"aimed_{kind}"], stops[f"actual_{kind}"], stops[f"expected_{kind}"], cancelled
        )
        stops[f"{kind}_time"] = time
        stops[f"{kind}_source"] = source
        stops[f"{kind}_delay_s"] = (time - stops[f"aimed_{kind}"]).dt.total_seconds()

    columns = [
        "stop_point_ref", "aimed_arrival", "arrival_time", "arrival_source", "arrival_delay_s",
        "aimed_departure", "departure_time", "departure_source", "departure_delay_s",
        "call_cancelled", "journey_cancelled", "last_expected_made_at",
        "first_seen", "last_seen", "n_snapshots",
    ]
    return stops[columns].reset_index()


def report_quality(df: pd.DataFrame, journeys: pd.DataFrame) -> None:
    fetches = df["fetched_at"].drop_duplicates()
    # Bound the snapshots by the day's first and last aimed time, so a collector that
    # started late or stopped early also shows up as a gap.
    aimed = pd.concat([journeys["aimed_arrival"], journeys["aimed_departure"]]).dropna()
    points = pd.concat([pd.Series([aimed.min(), aimed.max()]), fetches])
    points = points.sort_values().reset_index(drop=True)
    gaps = points.diff()
    big = gaps[gaps > GAP_WARNING]
    log.info("collection: %d snapshots, %d gaps > %s", len(fetches), len(big), GAP_WARNING)
    for end, gap in big.items():
        log.warning("  gap of %s from %s to %s", gap, points[end - 1], points[end])

    for kind in ("arrival", "departure"):
        shares = journeys[f"{kind}_source"].value_counts(normalize=True).round(3).to_dict()
        log.info("%s sources: %s", kind, shares)

    has_actual = (journeys["arrival_source"] == "actual") | (journeys["departure_source"] == "actual")
    share = has_actual.groupby(journeys["dated_vehicle_journey_ref"]).mean()
    log.info(
        "journeys: %d total, %d with < %.0f%% of stops observed",
        len(share), (share < MIN_ACTUAL_SHARE).sum(), MIN_ACTUAL_SHARE * 100,
    )


def build_day(parsed_dir: Path, out_dir: Path, dataset: str, day: date) -> Path:
    df = load_parsed(parsed_dir, dataset, day)
    journeys = build_journeys(df)
    report_quality(df, journeys)

    out_path = out_dir / "et" / dataset / f"date={day.isoformat()}.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_name(out_path.name + ".tmp")
    journeys.to_parquet(tmp_path, index=False)
    os.replace(tmp_path, out_path)
    log.info("wrote %s: %d stops", out_path, len(journeys))
    return out_path


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    args.add_argument("--date", required=True, type=date.fromisoformat, help="operating day, YYYY-MM-DD")
    args.add_argument("--dataset", default="OST")
    args.add_argument("--parsed-dir", default="data/parsed", type=Path)
    args.add_argument("--out-dir", default="data/journeys", type=Path)
    parsed = args.parse_args()
    build_day(parsed.parsed_dir, parsed.out_dir, parsed.dataset, parsed.date)


if __name__ == "__main__":
    main()
