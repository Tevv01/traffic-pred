"""Parse one day of raw SIRI ET snapshots into a single Parquet file.

Output has one row per stop (call) per snapshot. Nothing is deduplicated:
the history of what was known at each fetch is the point.

Run from the repo root with:  uv run parse --date 2026-10-23 [--dataset OST]
"""

import argparse
import gzip
import logging
import os
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from lxml import etree # type: ignore

log = logging.getLogger("parser")

NS = {"s": "http://www.siri.org.uk/siri"}

# Output column -> SIRI element inside a call.
TIME_COLUMNS = {
    "aimed_arrival": "AimedArrivalTime",
    "expected_arrival": "ExpectedArrivalTime",
    "actual_arrival": "ActualArrivalTime",
    "aimed_departure": "AimedDepartureTime",
    "expected_departure": "ExpectedDepartureTime",
    "actual_departure": "ActualDepartureTime",
}

UTC_TIMESTAMP = pa.timestamp("us", tz="UTC")

# Explicit schema, so every day's file has identical types even if a column is all null.
SCHEMA = pa.schema(
    [
        ("fetched_at", UTC_TIMESTAMP),
        ("response_timestamp", UTC_TIMESTAMP),
        ("data_frame_ref", pa.string()),
        ("dated_vehicle_journey_ref", pa.string()),
        ("journey_recorded_at", UTC_TIMESTAMP),
        ("journey_cancelled", pa.bool_()),
        ("call_type", pa.string()),
        ("stop_point_ref", pa.string()),
        ("order", pa.int32()),
        ("visit_number", pa.int32()),
        ("call_cancelled", pa.bool_()),
        *[(column, UTC_TIMESTAMP) for column in TIME_COLUMNS],
    ]
)

# Rows buffered in memory before being written as one Parquet row group.
FLUSH_EVERY_ROWS = 200_000

# Don't expand entities; the input comes from the network.
XML_PARSER = etree.XMLParser(resolve_entities=False)


def parse_time(text: str | None) -> datetime | None:
    """Parse an ISO 8601 timestamp to UTC. Raises ValueError if invalid or without an offset."""
    if text is None:
        return None
    value = datetime.fromisoformat(text)
    # Without an offset, astimezone() would silently assume the machine's local time.
    if value.tzinfo is None:
        raise ValueError(f"timestamp without UTC offset: {text!r}")
    return value.astimezone(timezone.utc)


def parse_int(text: str | None) -> int | None:
    return int(text) if text is not None else None


def fetched_at_from_path(path: Path, day: date) -> datetime:
    # Collector file names look like "110950Z.xml.gz" (UTC time of the fetch).
    hhmmss = path.name.split("Z")[0]
    clock = datetime.strptime(hhmmss, "%H%M%S").time()
    return datetime.combine(day, clock, tzinfo=timezone.utc)


def parse_snapshot(xml: bytes, fetched_at: datetime) -> list[dict]:
    root = etree.fromstring(xml, XML_PARSER)
    if root.tag != f"{{{NS['s']}}}Siri":
        # E.g. a schema change upstream; would otherwise silently give zero rows.
        log.warning("unexpected root element %s, expected Siri in %s", root.tag, NS["s"])

    # A bad value nulls only that field, so one odd call doesn't cost the whole snapshot.
    bad_values: list[str] = []

    def safe[T](parse: Callable[[str | None], T], text: str | None) -> T | None:
        try:
            return parse(text)
        except ValueError:
            bad_values.append(str(text))
            return None

    response_timestamp = safe(
        parse_time, root.findtext("s:ServiceDelivery/s:ResponseTimestamp", namespaces=NS)
    )

    rows = []
    for journey in root.iterfind(".//s:EstimatedVehicleJourney", NS):
        journey_fields = {
            "fetched_at": fetched_at,
            "response_timestamp": response_timestamp,
            "data_frame_ref": journey.findtext(
                "s:FramedVehicleJourneyRef/s:DataFrameRef", namespaces=NS
            ),
            "dated_vehicle_journey_ref": journey.findtext(
                "s:FramedVehicleJourneyRef/s:DatedVehicleJourneyRef", namespaces=NS
            ),
            "journey_recorded_at": safe(parse_time, journey.findtext("s:RecordedAtTime", namespaces=NS)),
            "journey_cancelled": journey.findtext("s:Cancellation", namespaces=NS) == "true",
        }

        for call_type, path in (
            ("recorded", "s:RecordedCalls/s:RecordedCall"),
            ("estimated", "s:EstimatedCalls/s:EstimatedCall"),
        ):
            for call in journey.iterfind(path, NS):
                row = {
                    **journey_fields,
                    "call_type": call_type,
                    "stop_point_ref": call.findtext("s:StopPointRef", namespaces=NS),
                    "order": safe(parse_int, call.findtext("s:Order", namespaces=NS)),
                    "visit_number": safe(parse_int, call.findtext("s:VisitNumber", namespaces=NS)),
                    "call_cancelled": call.findtext("s:Cancellation", namespaces=NS) == "true",
                }
                for column, element in TIME_COLUMNS.items():
                    row[column] = safe(parse_time, call.findtext(f"s:{element}", namespaces=NS))
                rows.append(row)

    if bad_values:
        log.warning("%d unparseable values set to null, e.g. %s", len(bad_values), bad_values[:3])
    return rows


def parse_day(raw_dir: Path, out_dir: Path, dataset: str, day: date) -> Path:
    day_folder = raw_dir / "et" / dataset / f"date={day.isoformat()}"
    files = sorted(day_folder.glob("*.xml.gz"))
    if not files:
        raise FileNotFoundError(f"no raw files in {day_folder}")

    out_path = out_dir / "et" / dataset / f"date={day.isoformat()}.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file and rename at the end, so a failed run never leaves a partial file.
    tmp_path = out_path.with_name(out_path.name + ".tmp")

    total_rows = 0
    skipped = 0
    buffer: list[dict] = []
    with pq.ParquetWriter(tmp_path, SCHEMA) as writer:
        for path in files:
            try:
                buffer.extend(parse_snapshot(gzip.decompress(path.read_bytes()), fetched_at_from_path(path, day)))
            except (OSError, ValueError, etree.XMLSyntaxError):
                log.exception("skipping unreadable file %s", path)
                skipped += 1
                continue
            if len(buffer) >= FLUSH_EVERY_ROWS:
                writer.write_table(pa.Table.from_pylist(buffer, schema=SCHEMA))
                total_rows += len(buffer)
                buffer = []
        if buffer:
            writer.write_table(pa.Table.from_pylist(buffer, schema=SCHEMA))
            total_rows += len(buffer)

    os.replace(tmp_path, out_path)
    log.info(
        "wrote %s: %d rows from %d files (%d skipped)",
        out_path, total_rows, len(files) - skipped, skipped,
    )
    return out_path


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = argparse.ArgumentParser(description=__doc__.splitlines()[0]) # type: ignore
    args.add_argument("--date", required=True, type=date.fromisoformat, help="UTC day, YYYY-MM-DD")
    args.add_argument("--dataset", default="OST")
    args.add_argument("--raw-dir", default="data/raw", type=Path)
    args.add_argument("--out-dir", default="data/parsed", type=Path)
    parsed = args.parse_args()
    parse_day(parsed.raw_dir, parsed.out_dir, parsed.dataset, parsed.date)


if __name__ == "__main__":
    main()
