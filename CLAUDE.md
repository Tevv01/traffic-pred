# CLAUDE.md

## About me and this project

I'm Vetle, a junior AI/ML engineer (MSc in Robotics and Intelligent Systems, UiO; background in PyTorch, evolutionary computation and neural architecture search). I work as a consultant at Capgemini, and I'm building this project to gain real, production-grade ML engineering experience and to have something solid on my CV.

**The project:** predict delays for Norwegian public transport using Entur's open real-time data, with a full MLOps lifecycle:
ingestion → feature pipeline → training → model registry → serving → monitoring → retraining.

The ML model itself can be simple. The point is the engineering around it.


## Language

- Write everything in the repo in **English**: code, comments, docstrings, commit messages, README and docs.

## Roadmap

1. **Collector**: a service that runs 24/7, polls Entur real-time data and stores raw snapshots (Parquet in blob storage), with basic data-quality checks and alerting. *Highest priority, because history only exists from the day this starts running.*
2. **Data model and features**: join planned timetables with observed times. Example target: "given the current delay, what will the delay be N stops ahead?"
3. **Baselines, then model**: "delay persists" and "historical median for this departure" first, then a real model (e.g. gradient boosting).
4. **Serving**: FastAPI in a container, model loaded from a registry (e.g. MLflow), CI/CD.
5. **Monitoring and retraining**: live accuracy, drift detection, automated retraining.

**Current phase: 2.** Remind me to update this line when we move on.

## Domain facts

- Entur real-time data is available as SIRI (XML and SIRI Lite/REST) and GTFS-RT. Types: ET (Estimated Timetable), VM (Vehicle Monitoring), SX (Situation Exchange).
- **ET only covers the current operational day. The API has no history**, so we must collect it ourselves.
- With plain HTTP GET polling, the `requestorId` parameter returns only changes since the last request. Without it, you get the full dataset every time.
- Clients should identify themselves with the `ET-Client-Name` header. Check developer.entur.org for current requirements.
- Planned (static) timetable data comes as NeTEx or GTFS.
- Ground truth arrives minutes after a prediction. This makes continuous measurement of live production accuracy possible, which is a key strength of the project.
- Entur's OpenTripPlanner has an "empirical delay" feature (historical typical delay per trip and stop). Beating that kind of baseline is the bar.

When unsure about Entur's API details, tell me to check the official docs. Don't guess.

## What "production-ready" means here

These are starting defaults. If I want to change one, challenge my reasoning, but the decision is mine.

- Python, with `uv` for dependencies, `ruff` for linting/formatting, type hints checked with `mypy` or `pyright`, and `pytest`.
- Tests for parsing and transformation logic, using real sample SIRI files as fixtures.
- **Time-based train/test splits only.** Flag any risk of data leakage loudly.
- Configuration via environment variables. **Secrets never in the repo**: Azure Key Vault in the cloud, a git-ignored `.env` locally.
- Everything containerized. CI runs lint and tests, and later model-evaluation gates.
- Azure infrastructure as code (Bicep or Terraform). This doubles as AZ-104 practice, so point out relevant Azure concepts when they come up.
- Structured logging and data-quality checks from the start.
- Architecture decisions recorded as short ADRs in `docs/adr/`. **I write them; you review them.**

## Safety and cost

- Ask before running anything that creates or changes cloud resources, deletes data, or pushes to git.
- Before I create an Azure resource, mention the expected cost and the cheapest sensible tier.
- Never print, log or commit secrets.

## Session wrap-up

When I say "summarize", give me a short summary: what I learned, open questions, and the next step. I'll paste it into `docs/learning-log.md`.