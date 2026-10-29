import os
import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "changestream.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"

REQUIRED_FIELDS = {
    "event_id",
    "customer_id",
    "operation",
    "version",
    "event_time",
}
OPTIONAL_OPERATION_FIELDS = {"name", "email", "amount"}
ALLOWED_FIELDS = REQUIRED_FIELDS | OPTIONAL_OPERATION_FIELDS


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def get_connection():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def initialize_database():
    with get_connection() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_event(event):
    if not isinstance(event, dict):
        return "Record must be a JSON object"

    missing = REQUIRED_FIELDS - event.keys()
    if missing:
        return "Missing required fields: " + ", ".join(sorted(missing))

    unexpected = set(event.keys()) - ALLOWED_FIELDS
    if unexpected:
        return "Unexpected fields: " + ", ".join(sorted(unexpected))

    for field in ("event_id", "customer_id", "event_time"):
        if not isinstance(event[field], str) or not event[field].strip():
            return f"{field} must be a non-empty string"

    if event["operation"] not in {"INSERT", "UPDATE", "DELETE"}:
        return "operation must be INSERT, UPDATE, or DELETE"

    if isinstance(event["version"], bool) or not isinstance(event["version"], int):
        return "version must be an integer"

    if event["version"] < 1:
        return "version must be greater than zero"

    if event["operation"] != "DELETE":
        for field in ("name", "email", "amount"):
            if field not in event or event[field] is None:
                return f"{field} is required for {event['operation']}"

        if not isinstance(event["name"], str) or not event["name"].strip():
            return "name must be a non-empty string"

        if not isinstance(event["email"], str) or "@" not in event["email"]:
            return "email must be a valid-looking email address"

        if isinstance(event["amount"], bool):
            return "amount must be numeric"

        try:
            amount = float(event["amount"])
        except (TypeError, ValueError):
            return "amount must be numeric"

        if amount < 0:
            return "amount cannot be negative"

    try:
        datetime.fromisoformat(event["event_time"].replace("Z", "+00:00"))
    except ValueError:
        return "event_time must be an ISO-8601 timestamp"

    return None


def _quarantine(conn, run_id, source_file, line_number, raw, event_id, reason):
    conn.execute(
        """
        INSERT INTO quarantine_events
        (run_id, source_file, line_number, event_id,
         error_reason, raw_record, quarantined_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            source_file,
            line_number,
            event_id,
            reason,
            raw,
            utc_now(),
        ),
    )


def _record_event(conn, event, run_id, source_file, outcome):
    now = utc_now()
    payload = json.dumps(event, sort_keys=True, default=str)

    conn.execute(
        """
        INSERT INTO event_history
        (event_id, customer_id, operation, event_version,
         event_time, processed_at, outcome, source_file, payload_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event["event_id"],
            event["customer_id"],
            event["operation"],
            event["version"],
            event["event_time"],
            now,
            outcome,
            source_file,
            payload,
        ),
    )

    conn.execute(
        """
        INSERT INTO processed_events
        (event_id, customer_id, processed_at, outcome, run_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        (event["event_id"], event["customer_id"], now, outcome, run_id),
    )


def _apply_event(conn, event, run_id, source_file):
    customer_id = event["customer_id"]
    operation = event["operation"]
    version = event["version"]

    current = conn.execute(
        "SELECT * FROM customers_current WHERE customer_id = ?",
        (customer_id,),
    ).fetchone()

    if current and version <= current["version"]:
        outcome = "STALE"
        now = utc_now()
        conn.execute(
            """
            INSERT INTO processed_events
            (event_id, customer_id, processed_at, outcome, run_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (event["event_id"], customer_id, now, outcome, run_id),
        )
        return outcome

    if operation == "DELETE":
        name = current["name"] if current else None
        email = current["email"] if current else None
        amount = current["amount"] if current else 0
        is_deleted = 1
    else:
        name = event["name"]
        email = event["email"]
        amount = float(event["amount"])
        is_deleted = 0

    now = utc_now()
    conn.execute(
        """
        INSERT INTO customers_current
        (customer_id, name, email, amount, version, is_deleted,
         last_event_id, last_event_time, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(customer_id) DO UPDATE SET
            name = excluded.name,
            email = excluded.email,
            amount = excluded.amount,
            version = excluded.version,
            is_deleted = excluded.is_deleted,
            last_event_id = excluded.last_event_id,
            last_event_time = excluded.last_event_time,
            updated_at = excluded.updated_at
        """,
        (
            customer_id,
            name,
            email,
            amount,
            version,
            is_deleted,
            event["event_id"],
            event["event_time"],
            now,
        ),
    )

    _record_event(conn, event, run_id, source_file, "APPLIED")
    return "APPLIED"


def process_file(file_path, reset_db=False):
    if reset_db and DB_PATH.exists():
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(DB_PATH) + suffix)
            if candidate.exists():
                candidate.unlink()

    initialize_database()

    path = Path(file_path)
    if not path.is_absolute():
        path = BASE_DIR / path

    if not path.is_file():
        raise FileNotFoundError(f"Input file not found: {path}")

    if reset_db:
        with get_connection() as conn:
            conn.execute("DELETE FROM processed_events")
            conn.execute("DELETE FROM event_history")
            conn.execute("DELETE FROM quarantine_events")
            conn.execute("DELETE FROM pipeline_runs")
            conn.execute("DELETE FROM customers_current")

    run_id = str(uuid.uuid4())
    started = utc_now()
    start_clock = time.perf_counter()

    metrics = {
        "input_count": 0,
        "accepted_count": 0,
        "applied_count": 0,
        "duplicate_count": 0,
        "stale_count": 0,
        "rejected_count": 0,
    }

    source_file = path.name

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO pipeline_runs
            (run_id, source_file, started_at, status)
            VALUES (?, ?, ?, ?)
            """,
            (run_id, source_file, started, "RUNNING"),
        )

        try:
            for line_number, raw_line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if not raw_line.strip():
                    continue

                metrics["input_count"] += 1
                savepoint = f"event_{line_number}"
                conn.execute(f"SAVEPOINT {savepoint}")

                try:
                    event = json.loads(raw_line)
                    reason = validate_event(event)

                    if reason:
                        _quarantine(
                            conn, run_id, source_file, line_number,
                            raw_line, event.get("event_id") if isinstance(event, dict) else None,
                            reason,
                        )
                        metrics["rejected_count"] += 1
                        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                        continue

                    metrics["accepted_count"] += 1

                    already_processed = conn.execute(
                        "SELECT 1 FROM processed_events WHERE event_id = ?",
                        (event["event_id"],),
                    ).fetchone()

                    if already_processed:
                        metrics["duplicate_count"] += 1
                        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                        continue

                    outcome = _apply_event(conn, event, run_id, source_file)

                    if outcome == "APPLIED":
                        metrics["applied_count"] += 1
                    elif outcome == "STALE":
                        metrics["stale_count"] += 1

                    conn.execute(f"RELEASE SAVEPOINT {savepoint}")

                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                    _quarantine(
                        conn, run_id, source_file, line_number,
                        raw_line, None, f"Malformed record: {exc}",
                    )
                    metrics["rejected_count"] += 1

            duration = time.perf_counter() - start_clock
            ended = utc_now()

            conn.execute(
                """
                UPDATE pipeline_runs SET
                    ended_at = ?, status = ?, input_count = ?,
                    accepted_count = ?, applied_count = ?,
                    duplicate_count = ?, stale_count = ?,
                    rejected_count = ?, duration_seconds = ?
                WHERE run_id = ?
                """,
                (
                    ended, "SUCCEEDED", metrics["input_count"],
                    metrics["accepted_count"], metrics["applied_count"],
                    metrics["duplicate_count"], metrics["stale_count"],
                    metrics["rejected_count"], duration, run_id,
                ),
            )
            conn.commit()

        except Exception as exc:
            # Roll back partial data changes, then persist a separate failure
            # record so operators can see the failed attempt and its reason.
            conn.rollback()
            try:
                with get_connection() as failure_conn:
                    failure_conn.execute(
                        """
                        INSERT OR REPLACE INTO pipeline_runs
                        (run_id, source_file, started_at, ended_at, status,
                         input_count, accepted_count, applied_count,
                         duplicate_count, stale_count, rejected_count,
                         duration_seconds, error_message)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run_id, source_file, started, utc_now(), "FAILED",
                            metrics["input_count"], metrics["accepted_count"],
                            metrics["applied_count"], metrics["duplicate_count"],
                            metrics["stale_count"], metrics["rejected_count"],
                            round(time.perf_counter() - start_clock, 4),
                            str(exc)[:2000],
                        ),
                    )
            except Exception:
                # Preserve the original processing error if failure logging
                # itself is impossible (for example, a database outage).
                pass
            raise RuntimeError(f"Pipeline run {run_id} failed: {exc}") from exc

    report = get_reconciliation()
    return {
        "run_id": run_id,
        "source_file": source_file,
        "started_at": started,
        "status": "SUCCEEDED",
        **metrics,
        "duration_seconds": round(time.perf_counter() - start_clock, 4),
        "reconciliation": report,
    }


def get_reconciliation():
    initialize_database()

    with get_connection() as conn:
        active = conn.execute(
            "SELECT COUNT(*) FROM customers_current WHERE is_deleted = 0"
        ).fetchone()[0]

        deleted = conn.execute(
            "SELECT COUNT(*) FROM customers_current WHERE is_deleted = 1"
        ).fetchone()[0]

        history = conn.execute(
            "SELECT COUNT(*) FROM event_history"
        ).fetchone()[0]

        processed = conn.execute(
            "SELECT COUNT(*) FROM processed_events"
        ).fetchone()[0]

        quarantined = conn.execute(
            "SELECT COUNT(*) FROM quarantine_events"
        ).fetchone()[0]

        mismatch = conn.execute(
            """
            SELECT COUNT(*)
            FROM customers_current c
            LEFT JOIN (
                SELECT customer_id, MAX(event_version) AS max_version
                FROM event_history
                GROUP BY customer_id
            ) h ON h.customer_id = c.customer_id
            WHERE h.max_version IS NULL OR h.max_version != c.version
            """
        ).fetchone()[0]

    return {
        "active_customers": active,
        "deleted_tombstones": deleted,
        "history_events": history,
        "processed_event_registry": processed,
        "quarantined_records": quarantined,
        "current_state_history_mismatches": mismatch,
        "checks_passed": mismatch == 0,
    }
