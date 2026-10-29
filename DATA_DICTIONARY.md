# Data Dictionary

All table and column names use `snake_case`. Source timestamps must be ISO-8601 parseable strings. `run_id` values are UUIDs generated per pipeline execution.

## Source event contract (JSONL)

| Field | Type | Required | Rule |
|---|---|---:|---|
| `event_id` | string | yes | Stable event identifier; duplicate IDs are not applied twice |
| `customer_id` | string | yes | Non-empty customer/business key |
| `operation` | string | yes | `INSERT`, `UPDATE`, or `DELETE` |
| `version` | integer | yes | Positive, monotonically increasing version per customer |
| `event_time` | string | yes | ISO-8601 parseable timestamp |
| `name` | string | insert/update | Non-empty |
| `email` | string | insert/update | Must contain `@` (basic validation only) |
| `amount` | number | insert/update | Numeric and non-negative |

Unexpected fields are rejected to make schema drift visible. A delete event requires the five common fields and may omit `name`, `email`, and `amount`.

## Target tables

### `customers_current` — one row per customer key
- `customer_id` TEXT PRIMARY KEY — stable business key.
- `name`, `email` TEXT — latest values; nullable for delete tombstones.
- `amount` REAL — latest amount; defaults to zero for a delete with no prior state.
- `version` INTEGER — version of latest applied state.
- `is_deleted` INTEGER — `0` active, `1` tombstone; constrained to these values.
- `last_event_id` TEXT — ID of the event that established current state.
- `last_event_time`, `updated_at` TEXT — source event time and processing/update time.

### `event_history` — one row per applied event
- `history_id` INTEGER PRIMARY KEY AUTOINCREMENT — local audit sequence.
- `event_id` TEXT UNIQUE — source event identifier.
- `customer_id` TEXT — customer key.
- `operation` TEXT — insert/update/delete.
- `event_version` INTEGER — source version.
- `event_time`, `processed_at` TEXT — source and processing timestamps.
- `outcome` TEXT — applied outcome.
- `source_file` TEXT — source file name.
- `payload_json` TEXT — normalized JSON payload.
- SQLite triggers prevent ordinary updates and deletes.

### `processed_events` — idempotency registry
- `event_id` TEXT PRIMARY KEY — prevents reapplication.
- `customer_id` TEXT — customer key.
- `processed_at` TEXT — processing timestamp.
- `outcome` TEXT — applied or stale result.
- `run_id` TEXT — pipeline execution that first processed the ID.

### `quarantine_events` — rejected source records
- `quarantine_id` INTEGER PRIMARY KEY AUTOINCREMENT.
- `run_id` TEXT — pipeline execution.
- `source_file` TEXT — source filename.
- `line_number` INTEGER — one-based source line number.
- `event_id` TEXT — event ID when it can be parsed.
- `error_reason` TEXT — validation/parsing failure.
- `raw_record` TEXT — original line for traceability.
- `quarantined_at` TEXT — timestamp of quarantine.

### `pipeline_runs` — one row per file execution
- `run_id` TEXT PRIMARY KEY — generated UUID.
- `source_file` TEXT — input filename.
- `started_at`, `ended_at` TEXT — execution timestamps.
- `status` TEXT — run state.
- `input_count`, `accepted_count`, `applied_count`, `duplicate_count`, `stale_count`, `rejected_count` INTEGER — operational counters.
- `duration_seconds` REAL — elapsed processing time.
- `error_message` TEXT — reserved for failure details.

## Relationships and integrity

- `customers_current.customer_id` is the current-state grain and primary key.
- `event_history` is event-grain; `event_id` is unique.
- `processed_events.event_id` is unique and provides idempotency.
- `pipeline_runs.run_id` identifies a run; quarantine rows record the associated run.
- There is no external customer master table, so customer IDs are validated for presence/format, not against an independent reference dataset.
