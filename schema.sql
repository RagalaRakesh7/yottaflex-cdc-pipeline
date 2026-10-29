
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS customers_current (
    customer_id TEXT PRIMARY KEY,
    name TEXT,
    email TEXT,
    amount REAL NOT NULL DEFAULT 0,
    version INTEGER NOT NULL,
    is_deleted INTEGER NOT NULL DEFAULT 0
        CHECK (is_deleted IN (0, 1)),
    last_event_id TEXT NOT NULL,
    last_event_time TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    customer_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    event_version INTEGER NOT NULL,
    event_time TEXT NOT NULL,
    processed_at TEXT NOT NULL,
    outcome TEXT NOT NULL,
    source_file TEXT,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processed_events (
    event_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    processed_at TEXT NOT NULL,
    outcome TEXT NOT NULL,
    run_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quarantine_events (
    quarantine_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    source_file TEXT,
    line_number INTEGER,
    event_id TEXT,
    error_reason TEXT NOT NULL,
    raw_record TEXT NOT NULL,
    quarantined_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,
    source_file TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL,
    input_count INTEGER NOT NULL DEFAULT 0,
    accepted_count INTEGER NOT NULL DEFAULT 0,
    applied_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    stale_count INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0,
    duration_seconds REAL NOT NULL DEFAULT 0,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_history_customer
    ON event_history(customer_id, event_version);

CREATE INDEX IF NOT EXISTS idx_runs_started
    ON pipeline_runs(started_at);

CREATE INDEX IF NOT EXISTS idx_quarantine_run
    ON quarantine_events(run_id);

-- Prevent accidental changes to recorded audit events.
CREATE TRIGGER IF NOT EXISTS prevent_history_update
BEFORE UPDATE ON event_history
BEGIN
    SELECT RAISE(ABORT, 'Audit history is immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_history_delete
BEFORE DELETE ON event_history
BEGIN
    SELECT RAISE(ABORT, 'Audit history is immutable');
END;
