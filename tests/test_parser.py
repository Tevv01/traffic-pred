"""Tests for parsing raw SIRI ET snapshots into rows."""

import gzip
import logging
from datetime import date, datetime, timezone

import pyarrow.parquet as pq
import pytest

from traffic_pred.parser import parse_day, parse_snapshot

SIRI_NS = "http://www.siri.org.uk/siri"
FETCHED_AT = datetime(2026, 10, 23, 7, 30, tzinfo=timezone.utc)
DAY = date(2026, 10, 23)


def utc(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 10, 23, hour, minute, second, tzinfo=timezone.utc)


# ---------- Helpers that build minimal SIRI documents ----------


def siri(journeys: str = "", namespace: str = SIRI_NS) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Siri xmlns="{namespace}" version="2.0">
  <ServiceDelivery>
    <ResponseTimestamp>2026-10-23T09:30:01+02:00</ResponseTimestamp>
    <EstimatedTimetableDelivery version="2.0">
      <EstimatedJourneyVersionFrame>{journeys}</EstimatedJourneyVersionFrame>
    </EstimatedTimetableDelivery>
  </ServiceDelivery>
</Siri>""".encode()


def journey(calls: str, cancellation: str | None = "false", ref: str = "OST:ServiceJourney:1") -> str:
    cancellation_xml = f"<Cancellation>{cancellation}</Cancellation>" if cancellation is not None else ""
    return f"""
<EstimatedVehicleJourney>
  <RecordedAtTime>2026-10-23T09:29:00+02:00</RecordedAtTime>
  <LineRef>OST:Line:1_115</LineRef>
  <FramedVehicleJourneyRef>
    <DataFrameRef>2026-10-23</DataFrameRef>
    <DatedVehicleJourneyRef>{ref}</DatedVehicleJourneyRef>
  </FramedVehicleJourneyRef>
  {cancellation_xml}
  {calls}
</EstimatedVehicleJourney>"""


def recorded(*calls: str) -> str:
    return "<RecordedCalls>" + "".join(f"<RecordedCall>{c}</RecordedCall>" for c in calls) + "</RecordedCalls>"


def estimated(*calls: str) -> str:
    return "<EstimatedCalls>" + "".join(f"<EstimatedCall>{c}</EstimatedCall>" for c in calls) + "</EstimatedCalls>"


def call(order: str = "1", stop: str = "NSR:Quay:1", **times: str) -> str:
    """A call body; keyword arguments are SIRI time elements, e.g. AimedDepartureTime=..."""
    time_xml = "".join(f"<{name}>{value}</{name}>" for name, value in times.items())
    return f"<StopPointRef>{stop}</StopPointRef><Order>{order}</Order>{time_xml}"


# ---------- parse_snapshot: normal cases ----------


def test_recorded_call_has_actual_times():
    xml = siri(journey(recorded(call(
        AimedDepartureTime="2026-10-23T09:20:00+02:00",
        ActualDepartureTime="2026-10-23T09:23:08+02:00",
    ))))

    [row] = parse_snapshot(xml, FETCHED_AT)

    assert row["call_type"] == "recorded"
    assert row["aimed_departure"] == utc(7, 20)
    assert row["actual_departure"] == utc(7, 23, 8)
    assert row["expected_departure"] is None


def test_estimated_call_has_expected_times():
    xml = siri(journey(estimated(call(
        AimedArrivalTime="2026-10-23T09:36:00+02:00",
        ExpectedArrivalTime="2026-10-23T09:40:28+02:00",
    ))))

    [row] = parse_snapshot(xml, FETCHED_AT)

    assert row["call_type"] == "estimated"
    assert row["expected_arrival"] == utc(7, 40, 28)
    assert row["actual_arrival"] is None


def test_one_row_per_call_with_journey_fields_on_each():
    xml = siri(journey(
        recorded(call(order="1", stop="NSR:Quay:1"))
        + estimated(call(order="2", stop="NSR:Quay:2"), call(order="3", stop="NSR:Quay:3"))
    ))

    rows = parse_snapshot(xml, FETCHED_AT)

    assert [r["order"] for r in rows] == [1, 2, 3]
    assert [r["stop_point_ref"] for r in rows] == ["NSR:Quay:1", "NSR:Quay:2", "NSR:Quay:3"]
    for r in rows:
        assert r["fetched_at"] == FETCHED_AT
        assert r["response_timestamp"] == utc(7, 30, 1)
        assert r["data_frame_ref"] == "2026-10-23"
        assert r["dated_vehicle_journey_ref"] == "OST:ServiceJourney:1"
        assert r["journey_recorded_at"] == utc(7, 29)


def test_first_stop_without_arrival_has_null_arrival_times():
    xml = siri(journey(recorded(call(
        AimedDepartureTime="2026-10-23T09:20:00+02:00",
        ActualDepartureTime="2026-10-23T09:23:08+02:00",
    ))))

    [row] = parse_snapshot(xml, FETCHED_AT)

    assert row["aimed_arrival"] is None
    assert row["actual_arrival"] is None


def test_times_are_converted_to_utc():
    xml = siri(journey(estimated(call(AimedDepartureTime="2026-10-23T09:20:00+02:00"))))

    [row] = parse_snapshot(xml, FETCHED_AT)

    assert row["aimed_departure"] == utc(7, 20)
    assert row["aimed_departure"].utcoffset().total_seconds() == 0


def test_nanosecond_timestamps_are_parsed():
    xml = siri(journey(estimated(call(ExpectedDepartureTime="2026-10-23T09:39:30.956078844+02:00"))))

    [row] = parse_snapshot(xml, FETCHED_AT)

    assert row["expected_departure"] == datetime(2026, 10, 23, 7, 39, 30, 956078, tzinfo=timezone.utc)


def test_journey_and_call_cancellations():
    cancelled_call = call() + "<Cancellation>true</Cancellation>"
    xml = siri(journey(estimated(cancelled_call), cancellation="true"))

    [row] = parse_snapshot(xml, FETCHED_AT)

    assert row["journey_cancelled"] is True
    assert row["call_cancelled"] is True


def test_missing_cancellation_means_not_cancelled():
    xml = siri(journey(estimated(call()), cancellation=None))

    [row] = parse_snapshot(xml, FETCHED_AT)

    assert row["journey_cancelled"] is False
    assert row["call_cancelled"] is False


def test_empty_delivery_gives_no_rows():
    assert parse_snapshot(siri(), FETCHED_AT) == []


# ---------- parse_snapshot: unexpected data ----------


def test_missing_order_is_null():
    xml = siri(journey(estimated("<StopPointRef>NSR:Quay:1</StopPointRef>")))

    [row] = parse_snapshot(xml, FETCHED_AT)

    assert row["order"] is None


def test_bad_integer_nulls_only_that_field():
    xml = siri(journey(
        estimated(call(order="abc", AimedDepartureTime="2026-10-23T09:20:00+02:00"), call(order="2"))
    ))

    rows = parse_snapshot(xml, FETCHED_AT)

    assert len(rows) == 2
    assert rows[0]["order"] is None
    assert rows[0]["aimed_departure"] == utc(7, 20)
    assert rows[1]["order"] == 2


def test_bad_timestamp_nulls_only_that_field():
    xml = siri(journey(estimated(call(
        AimedDepartureTime="not-a-time",
        ExpectedDepartureTime="2026-10-23T09:21:00+02:00",
    ))))

    [row] = parse_snapshot(xml, FETCHED_AT)

    assert row["aimed_departure"] is None
    assert row["expected_departure"] == utc(7, 21)


def test_timestamp_without_offset_is_not_guessed():
    # Without an offset the time is ambiguous; it must not be read as the machine's local time.
    xml = siri(journey(estimated(call(AimedDepartureTime="2026-10-23T09:20:00"))))

    [row] = parse_snapshot(xml, FETCHED_AT)

    assert row["aimed_departure"] is None


# ---------- parse_day ----------


def write_raw(raw_dir, name: str, xml: bytes):
    folder = raw_dir / "et" / "OST" / f"date={DAY.isoformat()}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(gzip.compress(xml))


def read_output(out_dir):
    return pq.read_table(out_dir / "et" / "OST" / f"date={DAY.isoformat()}.parquet").to_pylist()


def test_parse_day_takes_fetched_at_from_file_name(tmp_path):
    write_raw(tmp_path / "raw", "073000Z.xml.gz", siri(journey(estimated(call()))))

    parse_day(tmp_path / "raw", tmp_path / "out", "OST", DAY)

    [row] = read_output(tmp_path / "out")
    assert row["fetched_at"] == utc(7, 30)


def test_parse_day_combines_files_in_time_order(tmp_path):
    write_raw(tmp_path / "raw", "073100Z.xml.gz", siri(journey(estimated(call(order="2")))))
    write_raw(tmp_path / "raw", "073000Z.xml.gz", siri(journey(estimated(call(order="1")))))

    parse_day(tmp_path / "raw", tmp_path / "out", "OST", DAY)

    rows = read_output(tmp_path / "out")
    assert [r["order"] for r in rows] == [1, 2]


def test_parse_day_skips_corrupt_file_and_keeps_the_rest(tmp_path):
    good = siri(journey(estimated(call())))
    write_raw(tmp_path / "raw", "073000Z.xml.gz", good)
    write_raw(tmp_path / "raw", "073100Z.xml.gz", good[: len(good) // 2])  # truncated XML
    folder = tmp_path / "raw" / "et" / "OST" / f"date={DAY.isoformat()}"
    (folder / "073200Z.xml.gz").write_bytes(b"not gzip at all")

    parse_day(tmp_path / "raw", tmp_path / "out", "OST", DAY)

    assert len(read_output(tmp_path / "out")) == 1
    assert list((tmp_path / "out").rglob("*.tmp")) == []


def test_parse_day_without_raw_files_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_day(tmp_path / "raw", tmp_path / "out", "OST", DAY)


def test_wrong_namespace_is_warned_about(tmp_path, caplog):
    # A schema change upstream would otherwise silently produce zero rows.
    write_raw(tmp_path / "raw", "073000Z.xml.gz", siri(journey(estimated(call())), namespace="http://example.com/other"))

    with caplog.at_level(logging.WARNING):
        parse_day(tmp_path / "raw", tmp_path / "out", "OST", DAY)

    assert any(r.levelno >= logging.WARNING for r in caplog.records)


def test_empty_delta_is_not_warned_about(tmp_path, caplog):
    # A valid response with no changed journeys is normal (e.g. at night).
    write_raw(tmp_path / "raw", "073000Z.xml.gz", siri())

    with caplog.at_level(logging.WARNING):
        parse_day(tmp_path / "raw", tmp_path / "out", "OST", DAY)

    assert not any(r.levelno >= logging.WARNING for r in caplog.records)
