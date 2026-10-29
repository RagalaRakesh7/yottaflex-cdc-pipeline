# ChangeStream — CDC Operations Dashboard

A local Change Data Capture (CDC) pipeline and Flask operations dashboard for processing customer `INSERT`, `UPDATE`, and `DELETE` events into a current-state table while preserving an append-only audit history.

## Technology

* Python 3.11+
* Flask
* SQLite
* HTML, CSS, JavaScript
* pytest

## Architecture

```text
JSONL / NDJSON source
        |
        v
Read one record at a time
        |
        v
JSON parsing + schema/business validation
        | invalid
        +--------------------> quarantine\_events
        |
        v
event\_id duplicate check
        |
        v
customer version check (stale/out-of-order protection)
        |
        v
customers\_current (latest state / delete tombstone)
        |
        +----> event\_history (immutable applied-event ledger)
        +----> processed\_events (idempotency registry)
        +----> pipeline\_runs (counts, status, duration)
        |
        v
Reconciliation + Flask dashboard + CSV/JSON reports
```

## Repository layout

```text
YottaFlex/
├── app.py
├── cdc\_engine.py
├── data\_generator.py
├── schema.sql
├── requirements.txt
├── readme.md
├── DATA\_DICTIONARY.md
├── DESIGN_NOTES.md
├── data/
│   └── sample\_events.jsonl
├── reports/
│   └── sample\_run\_report.json
├── templates/
│   ├── base.html
│   ├── dashboard.html
│   ├── events.html
│   ├── event\_detail.html
│   ├── quarantine.html
│   └── runs.html
├── static/
│   ├── css/style.css
│   └── js/app.js
└── tests/
    └── test\_cdc\_engine.py
```

## Run locally on Windows

Open a terminal in the project folder:

```bat
py -m venv myenv
myenv\\Scripts\\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000`.

The Flask development server is intended for local evaluation only. Do not expose it directly to the public internet.

## Run the tests

```bat
python -m pytest -q
```

The automated test suite includes the CDC-engine tests and additional regression tests for missing customer IDs, tombstone behavior, and audit-history immutability. The latest verified run reported 17 passed in 1.26 seconds; rerun the tests to confirm the current result. The test suite uses temporary SQLite databases so tests do not intentionally modify your working database.

## Sample pipeline

From the dashboard, click **Run sample**. Or from a Python shell in the project directory:

```python
from cdc\_engine import process\_file
result = process\_file("data/sample\_events.jsonl")
print(result)
```

A reference output from the included 8-record sample is saved at `reports/sample\_run\_report.json`. Run IDs and durations vary per execution. Expected sample behavior is 8 input records, 4 applied, 1 duplicate, 1 stale, and 2 rejected; reconciliation reports zero current-state/history version mismatches.

## Generate scalable synthetic data

Generate 100,000 base events with a fixed seed:

```bat
python data\_generator.py --count 100000 --seed 42
```

The generator intentionally adds duplicates, stale versions, and invalid records according to its configurable rates. `total\_written` can therefore be larger than the requested base count. To process generated data, open the dashboard and select **Process generated data**.

The generator uses deterministic event IDs and a fixed pseudo-random seed. Timestamps and output are designed to be reproducible for a given seed and generator version.

## Dashboard routes

* `/` — operational overview and pipeline controls
* `/events` — searchable audit history
* `/events/<history\_id>` — event inspector
* `/quarantine` — quarantined source records and validation reasons
* `/runs` — run history and performance summary
* `/reports/history.csv` — audit-history export
* `/reports/runs.csv` — pipeline-run export
* `/reports/reconciliation.json` — reconciliation export
* `/api/metrics` and `/api/reconciliation` — JSON endpoints
* `/health` — basic local health endpoint

## Processing rules and assumptions

* `event\_id` is the stable unique identifier of a source event.
* `customer\_id` is the aggregate/business key. There is no separate customer master/reference table in this project, so referential integrity against an external customer table is outside the current scope.
* Each event has a positive integer `version` per customer. Versions less than or equal to the current version are classified as stale and do not overwrite the current state.
* An event ID already in `processed\_events` is treated as a duplicate and is not applied twice. The first successfully processed occurrence wins; conflicting payloads reusing the same ID are not applied again.
* Supported event fields are `event\_id`, `customer\_id`, `operation`, `version`, `event\_time`, and (for insert/update) `name`, `email`, `amount`. Unexpected fields are quarantined to expose schema drift.
* `INSERT` and `UPDATE` require non-empty name, a valid-looking email, and a non-negative numeric amount. `DELETE` does not require those fields.
* Deletes create a tombstone in `customers\_current` instead of physically deleting the row.
* Input timestamps must be ISO-8601 parseable. Stored timestamps are ISO-8601 strings.
* Invalid records are quarantined with source file, line number, raw record and validation reason.
* The audit history is protected against ordinary SQL `UPDATE` and `DELETE` operations by SQLite triggers. This is not cryptographic tamper-proofing.
* The current-state/history reconciliation checks that every current customer has a matching maximum history version. Stale events and duplicates are tracked in the processed registry, not as applied history rows.

## Performance measurement

The dashboard reports throughput as `input\_count / duration\_seconds` for the latest run. For a repeatable benchmark, record the event count, seed, Python version, machine details, run duration, and reconciliation outcome. Compare runs with the same seed and environment. Avoid claiming a universal throughput figure from one local run.

One reasonable optimization for larger volumes is to keep batched transactions and add/verify indexes for frequently filtered columns. The schema already indexes customer/version history, run start time, and quarantine run ID. Benchmark before adding indexes because each index also increases write cost.

## Known limitations

* SQLite and Flask's development server are for a local demonstration, not a multi-user production deployment.
* Processing is synchronous and file-based.
* There is no authentication, authorization, external source connector, customer master table, distributed scheduler, or cryptographic audit chain.
* A run-level unexpected exception rolls back the active database transaction; retry the input after investigating the error. Per-record validation failures are quarantined and do not stop other valid records.
* Performance depends on the local machine, database size, and filesystem. Re-run the benchmark in the final submission environment.

## Submission checklist

* Source code and dependency/setup files
* Reproducible sample input and generator
* Automated tests and execution evidence
* SQLite schema and data dictionary
* Sample output and reconciliation result
* Performance-measurement method and trade-off note
* Known limitations and next steps

