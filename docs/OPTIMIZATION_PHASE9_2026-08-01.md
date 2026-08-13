# Phase 9 — Benchmark Dataset and Shadow Calibration

## Purpose

Completed provider benchmark jobs are now exported as a local, auditable
dataset. The system then measures disagreement between automatic visual QC and
human review without calling any paid model API.

## Artifacts

- `backend/data/benchmark-datasets/<experiment-id>/manifest.jsonl`
- `backend/data/benchmark-datasets/<experiment-id>/calibration-report.json`
- `POST /api/projects/{project_id}/benchmarks/{experiment_id}/dataset/export`
- `GET /api/projects/{project_id}/benchmarks/{experiment_id}/dataset/report`

The JSONL manifest contains local output locations, execution time, actual
cost, automatic-QC output, operator review, blind review and failure
categories. It intentionally excludes prompts, credentials and temporary
provider URLs.

## Safety Decision

Operator review is provisional evidence. Blind review is the calibration source
of truth. Paid retry remains manual-only until the data includes at least three
blind labels, at least one blind failure, and no observed automatic-QC false
negatives. Exporting and reporting cannot submit a task or spend money.
