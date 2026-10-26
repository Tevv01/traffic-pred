"""Tests for bundling parsed snapshots into per-journey delay trajectories."""

import logging
from datetime import date

import pandas as pd
import pytest

from traffic_pred.journeys import build_journeys, load_parsed, report_quality

DAY = "2026-10-26"
NEXT_DAY = "2026-10-27"

TIME_COLUMNS = [
    "fetched_at", "response_timestamp", "journey_recorded_at",
    "aimed_arrival", "expected_arrival", "actual_arrival",
    "aimed_departure", "expected_departure", "actual_departure",
]


def t(clock: str, day: str = DAY) -> str:
    """'08:01:30' -> '2026-10-26T08:01:30Z'."""
    return f"{day}T{clock}Z"


def snapshots(*rows: dict) -> pd.DataFrame:
    """Build a parsed-snapshot DataFrame; each row only needs the fields that matter for the test."""
    defaults = {
        "fetched_at": t("08:00:00"),
        "response_timestamp": None,
        "data_frame_ref": DAY,
        "dated_vehicle_journey_ref": "J1",
        "journey_recorded_at": None,
        "journey_cancelled": False,
        "call_type": "estimated",
        "stop_point_ref": "NSR:Quay:1",
        "order": 1,
        "visit_number": None,
        "call_cancelled": False,
        "aimed_arrival": None,
        "expected_arrival": None,
        "actual_arrival": None,
        "aimed_departure": None,
        "expected_departure": None,
        "actual_departure": None,
    }
    df = pd.DataFrame([{**defaults, **row} for row in rows])
    for column in TIME_COLUMNS:
        df[column] = pd.to_datetime(df[column], utc=True)
    return df.sort_values("fetched_at")


def only_stop(journeys: pd.DataFrame) -> pd.Series:
    assert len(journeys) == 1
    return journeys.iloc[0]


# ---------- Sources and delays ----------


def test_actual_time_gives_actual_source_and_delay():
    df = snapshots({
        "call_type": "recorded",
        "aimed_departure": t("08:00:00"),
        "actual_departure": t("08:01:30"),
    })

    stop = only_stop(build_journeys(df))

    assert stop["departure_source"] == "actual"
    assert stop["departure_delay_s"] == 90


def test_latest_actual_time_wins():
    df = snapshots(
        {"fetched_at": t("08:02:00"), "call_type": "recorded",
         "aimed_departure": t("08:00:00"), "actual_departure": t("08:01:00")},
        {"fetched_at": t("08:03:00"), "call_type": "recorded",
         "aimed_departure": t("08:00:00"), "actual_departure": t("08:02:00")},
    )

    stop = only_stop(build_journeys(df))

    assert stop["departure_time"] == pd.Timestamp(t("08:02:00"))


def test_actual_beats_earlier_prediction():
    df = snapshots(
        {"fetched_at": t("07:58:00"), "aimed_departure": t("08:00:00"), "expected_departure": t("08:03:00")},
        {"fetched_at": t("08:02:00"), "call_type": "recorded",
         "aimed_departure": t("08:00:00"), "actual_departure": t("08:01:00")},
    )

    stop = only_stop(build_journeys(df))

    assert stop["departure_source"] == "actual"
    assert stop["departure_delay_s"] == 60


def test_no_actual_falls_back_to_last_prediction():
    df = snapshots(
        {"fetched_at": t("07:55:00"), "aimed_departure": t("08:00:00"), "expected_departure": t("08:03:00")},
        {"fetched_at": t("07:58:00"), "aimed_departure": t("08:00:00"), "expected_departure": t("08:04:00")},
    )

    stop = only_stop(build_journeys(df))

    assert stop["departure_source"] == "last_expected"
    assert stop["departure_time"] == pd.Timestamp(t("08:04:00"))
    assert stop["departure_delay_s"] == 240
    assert stop["last_expected_made_at"] == pd.Timestamp(t("07:58:00"))


@pytest.mark.parametrize("flag", ["call_cancelled", "journey_cancelled"])
def test_cancellation_gives_cancelled_source_and_no_time(flag):
    df = snapshots({"aimed_departure": t("08:00:00"), "expected_departure": t("08:03:00"), flag: True})

    stop = only_stop(build_journeys(df))

    assert stop["departure_source"] == "cancelled"
    assert pd.isna(stop["departure_time"])


def test_no_aimed_time_is_not_applicable():
    # Last stop of a journey: it has an arrival but no departure by design.
    df = snapshots({
        "call_type": "recorded",
        "aimed_arrival": t("08:00:00"),
        "actual_arrival": t("08:00:30"),
    })

    stop = only_stop(build_journeys(df))

    assert stop["arrival_source"] == "actual"
    assert stop["departure_source"] == "not_applicable"


def test_nothing_known_is_missing():
    df = snapshots({"aimed_departure": t("08:00:00")})

    stop = only_stop(build_journeys(df))

    assert stop["departure_source"] == "missing"
    assert pd.isna(stop["departure_delay_s"])


# ---------- Bundling ----------


def test_one_row_per_stop_across_snapshots():
    df = snapshots(
        {"fetched_at": t("08:00:00"), "order": 1}, {"fetched_at": t("08:00:00"), "order": 2},
        {"fetched_at": t("08:01:00"), "order": 1}, {"fetched_at": t("08:01:00"), "order": 2},
        {"fetched_at": t("08:02:00"), "order": 1},
    )

    journeys = build_journeys(df)

    assert list(journeys["order"]) == [1, 2]
    assert list(journeys["n_snapshots"]) == [3, 2]
    assert journeys.iloc[0]["first_seen"] == pd.Timestamp(t("08:00:00"))
    assert journeys.iloc[0]["last_seen"] == pd.Timestamp(t("08:02:00"))


def test_journeys_are_kept_apart():
    df = snapshots(
        {"dated_vehicle_journey_ref": "J1", "aimed_departure": t("08:00:00"), "expected_departure": t("08:01:00")},
        {"dated_vehicle_journey_ref": "J2", "aimed_departure": t("08:00:00"), "expected_departure": t("08:05:00")},
    )

    journeys = build_journeys(df).set_index("dated_vehicle_journey_ref")

    assert journeys.loc["J1", "departure_delay_s"] == 60
    assert journeys.loc["J2", "departure_delay_s"] == 300


def test_journey_past_midnight_is_bundled_from_both_files(tmp_path):
    folder = tmp_path / "et" / "OST"
    folder.mkdir(parents=True)
    # Before UTC midnight, in the D file: the journey's first stop is still ahead.
    snapshots(
        {"fetched_at": t("23:50:00"), "order": 1,
         "aimed_departure": t("23:55:00"), "expected_departure": t("23:56:00")},
    ).to_parquet(folder / f"date={DAY}.parquet", index=False)
    # After UTC midnight, in the D+1 file: same journey (operating day D), plus a journey of D+1.
    snapshots(
        {"fetched_at": t("00:10:00", NEXT_DAY), "order": 1, "call_type": "recorded",
         "aimed_departure": t("23:55:00"), "actual_departure": t("23:57:00")},
        {"fetched_at": t("00:10:00", NEXT_DAY), "order": 2, "call_type": "recorded",
         "aimed_arrival": t("00:05:00", NEXT_DAY), "actual_arrival": t("00:06:00", NEXT_DAY)},
        {"fetched_at": t("00:10:00", NEXT_DAY), "data_frame_ref": NEXT_DAY,
         "dated_vehicle_journey_ref": "J2", "aimed_departure": t("05:00:00", NEXT_DAY)},
    ).to_parquet(folder / f"date={NEXT_DAY}.parquet", index=False)

    journeys = build_journeys(load_parsed(tmp_path, "OST", date.fromisoformat(DAY)))

    assert set(journeys["dated_vehicle_journey_ref"]) == {"J1"}
    assert list(journeys["order"]) == [1, 2]
    assert journeys.iloc[0]["departure_source"] == "actual"
    assert journeys.iloc[0]["departure_delay_s"] == 120
    assert journeys.iloc[1]["arrival_delay_s"] == 60


# ---------- Quality report ----------


def test_report_flags_collector_stopping_early(caplog):
    df = snapshots(
        {"fetched_at": t("08:00:00"), "order": 1, "aimed_departure": t("08:00:00")},
        {"fetched_at": t("08:01:00"), "order": 2, "aimed_departure": t("12:00:00")},
    )

    with caplog.at_level(logging.WARNING):
        report_quality(df, build_journeys(df))

    assert any("gap" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)


def test_report_has_no_gap_warnings_for_continuous_collection(caplog):
    df = snapshots(*[
        {"fetched_at": t(f"08:0{m}:00"), "aimed_departure": t("08:00:00")} for m in range(6)
    ], {"fetched_at": t("08:06:00"), "order": 2, "aimed_departure": t("08:05:00")})

    with caplog.at_level(logging.WARNING):
        report_quality(df, build_journeys(df))

    assert not any(r.levelno == logging.WARNING for r in caplog.records)
