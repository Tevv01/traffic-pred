"""Tests for the collector loop, using a fake HTTP transport and a fake clock (no network)."""

import gzip
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from traffic_pred import collector

SIRI_OK = b'<?xml version="1.0"?><Siri xmlns="http://www.siri.org.uk/siri"><ServiceDelivery/></Siri>'
T0 = datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc)


class StopLoop(Exception):
    pass


def ok() -> httpx.Response:
    return httpx.Response(200, content=SIRI_OK)


def run_collector(monkeypatch, tmp_path, responses, times=None):
    """Run the collector for one poll per response; return the requestorIds sent and files saved.

    A response can be an httpx.Response or an exception to raise instead (e.g. a dropped
    connection). `times` are the clock readings for each poll; default is one minute apart.
    """
    monkeypatch.setenv("CLIENT_NAME", "test-client")
    monkeypatch.setenv("ENTUR_TIMETABLE_URL", "https://example.test/et")
    monkeypatch.setenv("DATASET_ID", "OST")
    monkeypatch.setenv("POLL_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    queue = list(responses)
    sent_ids = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent_ids.append(request.url.params["requestorId"])
        response = queue.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    real_client = httpx.Client
    monkeypatch.setattr(
        collector.httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    clock = iter(times or [T0 + timedelta(minutes=i) for i in range(len(responses))])

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return next(clock)

    monkeypatch.setattr(collector, "datetime", FakeDatetime)

    def fake_sleep(_seconds):
        if not queue:
            raise StopLoop

    monkeypatch.setattr(collector.time, "sleep", fake_sleep)

    with pytest.raises(StopLoop):
        collector.run()
    return sent_ids, sorted(tmp_path.rglob("*.xml.gz"))


def test_saves_valid_response_unchanged(monkeypatch, tmp_path):
    _, files = run_collector(monkeypatch, tmp_path, [ok()])

    assert len(files) == 1
    assert gzip.decompress(files[0].read_bytes()) == SIRI_OK


def test_file_path_uses_dataset_and_utc_date(monkeypatch, tmp_path):
    _, files = run_collector(monkeypatch, tmp_path, [ok()])

    assert files[0] == tmp_path / "et" / "OST" / "date=2026-10-26" / "080000Z.xml.gz"


def test_no_temp_files_left_behind(monkeypatch, tmp_path):
    run_collector(monkeypatch, tmp_path, [ok()])

    assert list(tmp_path.rglob("*.tmp")) == []


def test_keeps_requestor_id_while_polling_normally(monkeypatch, tmp_path):
    ids, files = run_collector(monkeypatch, tmp_path, [ok(), ok()])

    assert ids[0] == ids[1]
    assert len(files) == 2


def test_http_error_is_not_saved_and_gets_new_requestor_id(monkeypatch, tmp_path):
    ids, files = run_collector(monkeypatch, tmp_path, [httpx.Response(500), ok()])

    assert ids[0] != ids[1]
    assert len(files) == 1


def test_dropped_connection_is_not_saved_and_gets_new_requestor_id(monkeypatch, tmp_path):
    dropped = httpx.RemoteProtocolError("peer closed connection without sending complete message body")
    ids, files = run_collector(monkeypatch, tmp_path, [dropped, ok()])

    assert ids[0] != ids[1]
    assert len(files) == 1


@pytest.mark.parametrize(
    "body",
    [b"", b"<html><body>Service unavailable</body></html>", b'{"error": "oops"}'],
    ids=["empty", "html", "json"],
)
def test_non_siri_200_is_not_saved_and_gets_new_requestor_id(monkeypatch, tmp_path, body):
    ids, files = run_collector(monkeypatch, tmp_path, [httpx.Response(200, content=body), ok()])

    assert len(files) == 1
    assert ids[0] != ids[1]


def test_long_gap_gets_new_requestor_id(monkeypatch, tmp_path):
    times = [T0, T0 + timedelta(minutes=10)]
    ids, _ = run_collector(monkeypatch, tmp_path, [ok(), ok()], times=times)

    assert ids[0] != ids[1]


def test_short_gap_keeps_requestor_id(monkeypatch, tmp_path):
    times = [T0, T0 + timedelta(minutes=2)]
    ids, _ = run_collector(monkeypatch, tmp_path, [ok(), ok()], times=times)

    assert ids[0] == ids[1]
