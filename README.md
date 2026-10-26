# traffic-pred

Delay prediction for Norwegian public transport, built on Entur's open real-time data (SIRI ET).

## Setup

```
uv sync
cp .env.example .env   # then fill in the values
```

See [docs/pipeline.md](docs/pipeline.md) for how collection and the daily processing fit together.

## Collector

Polls the SIRI ET endpoint and stores each raw response as gzipped XML under
`data/raw/et/<dataset>/date=YYYY-MM-DD/<HHMMSS>Z.xml.gz` (UTC).

```
uv run collector
```

Configuration (environment variables):

| Variable | Default | Description |
|---|---|---|
| `CLIENT_NAME` | required | Sent as the `ET-Client-Name` header |
| `ENTUR_TIMETABLE_URL` | required | SIRI ET REST endpoint |
| `DATASET_ID` | `OST` | Entur dataset to poll |
| `POLL_INTERVAL_SECONDS` | `60` | Seconds between polls |
| `DATA_DIR` | `data/raw` | Root folder for raw snapshots |
