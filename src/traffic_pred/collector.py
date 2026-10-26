"""Poll Entur's SIRI ET endpoint and store each raw response as gzipped XML.

Run from the repo root with:  uv run collector   (stop with Ctrl+C)
"""

import gzip
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

log = logging.getLogger("collector")


@dataclass(frozen=True)
class Config:
    client_name: str
    url: str
    dataset_id: str
    interval_seconds: float
    data_dir: Path


def load_config() -> Config:
    load_dotenv()
    return Config(
        client_name=os.environ["CLIENT_NAME"],
        url=os.environ["ENTUR_TIMETABLE_URL"],
        dataset_id=os.environ.get("DATASET_ID", "OST"),
        interval_seconds=float(os.environ.get("POLL_INTERVAL_SECONDS", "60")),
        data_dir=Path(os.environ.get("DATA_DIR", "data/raw")),
    )


def fetch(client: httpx.Client, cfg: Config, requestor_id: str) -> bytes:
    response = client.get(
        cfg.url, params={"datasetId": cfg.dataset_id, "requestorId": requestor_id}
    )
    response.raise_for_status()
    return response.content


def save(content: bytes, cfg: Config, fetched_at: datetime) -> Path:
    folder = cfg.data_dir / "et" / cfg.dataset_id / f"date={fetched_at:%Y-%m-%d}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{fetched_at:%H%M%S}Z.xml.gz"
    # Write to a temp file first, then rename, so a crash never leaves a partial file.
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(gzip.compress(content))
    os.replace(tmp, path)
    return path


def main() -> None:
    try:
        run()
    except KeyboardInterrupt:
        log.info("stopped")


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config()
    # A fresh id per run: the first response is a full snapshot, later ones only changes.
    requestor_id = str(uuid.uuid4())
    log.info("starting dataset=%s interval=%ss", cfg.dataset_id, cfg.interval_seconds)

    # After a long pause (e.g. laptop sleep) the server-side bookmark may have expired,
    # so a new id is used to get a complete full snapshot instead of trusting the delta.
    max_gap = timedelta(seconds=3 * cfg.interval_seconds)
    last_success: datetime | None = None

    timeout = httpx.Timeout(120.0, connect=10.0)
    with httpx.Client(headers={"ET-Client-Name": cfg.client_name}, timeout=timeout) as client:
        while True:
            started = time.monotonic()
            fetched_at = datetime.now(timezone.utc)
            if last_success is not None and fetched_at - last_success > max_gap:
                log.warning("%s since last successful poll, requesting full snapshot", fetched_at - last_success)
                requestor_id = str(uuid.uuid4())
            try:
                content = fetch(client, cfg, requestor_id)
                if b"<Siri" not in content[:500]:
                    log.warning("unexpected response, not saved: %r", content[:200])
                    # Same reasoning as a failed poll: the changes may count as delivered.
                    requestor_id = str(uuid.uuid4())
                else:
                    path = save(content, cfg, fetched_at)
                    last_success = fetched_at
                    log.info(
                        "saved %s raw=%d bytes gz=%d bytes in %.1fs",
                        path, len(content), path.stat().st_size, time.monotonic() - started,
                    )
            except (httpx.HTTPError, OSError):
                log.exception("poll failed")
                # The server may count a failed response as delivered, so those changes
                # would be lost. A new id forces a full snapshot on the next poll.
                requestor_id = str(uuid.uuid4())

            time.sleep(max(0.0, cfg.interval_seconds - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
