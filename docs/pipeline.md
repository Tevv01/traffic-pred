# Data pipeline

How raw real-time data becomes per-journey delay trajectories, and when each step runs.

```
                    every minute, all day                       once a day, ~06:00 local
┌───────────┐   ┌──────────────────────────────┐    ┌─────────────────────────────────────────┐
│ Entur ET  │──▶│ collector                    │    │ parse --date D                          │
│ (SIRI)    │   │ data/raw/.../date=D/*.xml.gz │───▶│ parse --date D+1                        │
└───────────┘   └──────────────────────────────┘    │ journeys --date D                       │
                                                    │ data/parsed/...  data/journeys/...      │
                                                    └─────────────────────────────────────────┘
```

All dates in file paths are **UTC**. `D` is the operating day being processed; the daily job
runs on the morning of `D+1`.

## 1. Collector (continuous)

```
uv run collector
```

- Polls the SIRI ET endpoint every `POLL_INTERVAL_SECONDS` (default 60) using a `requestorId`, so
  after the first full snapshot each response contains only changes.
- Stores every response untouched as gzipped XML:
  `data/raw/et/<dataset>/date=YYYY-MM-DD/<HHMMSS>Z.xml.gz`.
- Takes a new `requestorId` (forcing a full snapshot) on start, after a failed poll, and after a
  pause longer than 3 × the interval (e.g. laptop sleep).

**It must run continuously.** Entur drops a journey from the feed about 10 minutes after it
completes, and predictions are overwritten as the vehicle moves. Anything not collected while it
happens is lost for good; the raw files are the only copy of history.

## 2. Daily job (morning of D+1)

Run in this order:

```
uv run parse    --date D
uv run parse    --date D+1
uv run journeys --date D
```

| Step | Input | Output | What it does |
|---|---|---|---|
| `parse --date D` | `data/raw/.../date=D/` | `data/parsed/.../date=D.parquet` | One row per stop per snapshot. No deduplication. |
| `parse --date D+1` | `data/raw/.../date=D+1/` | `data/parsed/.../date=D+1.parquet` | Same, for the part of D+1 collected so far. |
| `journeys --date D` | parsed D and D+1 | `data/journeys/.../date=D.parquet` | One row per stop of each journey on operating day D, with final times, delays and their source. Logs a data-quality report. |

**Why D+1 is parsed too:** journeys are grouped by Entur's operating day (`data_frame_ref`), and
late trips of day D run past UTC midnight, so their last snapshots land in the D+1 raw folder.
The D+1 parsed file is partial at this point; it is overwritten when D+1 itself is processed the
next morning.

**Why ~06:00 local:** the last trips of operating day D finish in the early hours of D+1, and
stay in the feed about 10 minutes after that. By 06:00 they are all collected.

## Re-running and backfilling

Every step only reads its inputs and fully replaces its output (written to a `.tmp` file and
renamed), so any step can be re-run for any date at any time, e.g. after fixing a bug in the
parser:

```
uv run parse --date 2026-10-26
uv run parse --date 2026-10-27
uv run journeys --date 2026-10-26
```

Raw files are never modified or deleted by the pipeline.

## Reading the data-quality report

`journeys` logs, per day:

- **Collection gaps** longer than 3 minutes, including before the first and after the last
  snapshot (bounded by the day's earliest and latest aimed time). Each gap means lost predictions,
  and gaps over ~10 minutes also mean lost actual times.
- **Source shares** for arrivals and departures:
  - `actual`: observed time. The only source to use as a training label.
  - `last_expected`: no actual time; Entur's last prediction before the stop was passed.
  - `cancelled`: the stop or journey was cancelled.
  - `not_applicable`: no aimed time, e.g. departure from the last stop.
  - `missing`: nothing known.
- **Journeys with < 80% of stops observed** (`actual`).

A sudden rise in `last_expected` or `missing` points to a collection gap (check the gap list) or
a problem at the source (check whether it is limited to one operator or line).

## Scheduling on Windows

Use Task Scheduler with the repo root as the working directory ("Start in"):

- **Collector:** trigger *At log on*; action `uv run collector`; settings *If the task fails,
  restart every 1 minute*, and no *Stop the task if it runs longer than* limit.
- **Daily job:** trigger *Daily at 06:00*; action runs the three commands above with yesterday's
  and today's date.

Also make sure the machine does not sleep while plugged in.

## Open questions

- **Operating days around midnight:** so far only midday snapshots have been checked, and each
  UTC day contained only its own `data_frame_ref`. Once a full night has been collected, check
  whether journeys of operating day D also appear in the D-1 raw folder (e.g. trips just after
  local midnight, which is 22:00/23:00 UTC). If they do, `journeys` must read D-1 as well.
- **`requestorId` expiry:** how long Entur keeps the server-side bookmark is not yet confirmed
  in the docs. The gap rule above makes the collector safe either way.
