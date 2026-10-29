
import json

import cdc_engine


def setup_test_db(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    monkeypatch.setattr(cdc_engine, "DATA_DIR", data_dir)
    monkeypatch.setattr(cdc_engine, "DB_PATH", data_dir / "test.db")
    cdc_engine.initialize_database()
    return data_dir


def event(event_id, customer_id, operation, version, **extra):
    result = {
        "event_id": event_id,
        "customer_id": customer_id,
        "operation": operation,
        "version": version,
        "event_time": "2026-10-29T09:00:00Z",
    }
    if operation != "DELETE":
        result.update({
            "name": "Test Customer",
            "email": "test@example.com",
            "amount": 100.0,
        })
    result.update(extra)
    return result


def write_jsonl(path, records):
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            if isinstance(record, str):
                file.write(record + "\n")
            else:
                file.write(json.dumps(record) + "\n")


def test_insert_and_update(monkeypatch, tmp_path):
    data_dir = setup_test_db(monkeypatch, tmp_path)
    source = tmp_path / "events.jsonl"

    write_jsonl(source, [
        event("e1", "C1", "INSERT", 1),
        event("e2", "C1", "UPDATE", 2, amount=250.0),
    ])

    result = cdc_engine.process_file(source)

    assert result["applied_count"] == 2
    assert result["reconciliation"]["checks_passed"] is True

    with cdc_engine.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM customers_current WHERE customer_id='C1'"
        ).fetchone()

    assert row["version"] == 2
    assert row["amount"] == 250.0
    assert row["is_deleted"] == 0


def test_duplicate_event_is_not_applied_twice(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    source = tmp_path / "events.jsonl"
    first = event("same-id", "C1", "INSERT", 1)

    write_jsonl(source, [first, first])
    result = cdc_engine.process_file(source)

    assert result["applied_count"] == 1
    assert result["duplicate_count"] == 1
    assert result["reconciliation"]["history_events"] == 1


def test_stale_update_does_not_overwrite_latest(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    source = tmp_path / "events.jsonl"

    write_jsonl(source, [
        event("e2", "C1", "UPDATE", 2, amount=200.0),
        event("e1", "C1", "INSERT", 1, amount=100.0),
    ])

    result = cdc_engine.process_file(source)

    assert result["stale_count"] == 1

    with cdc_engine.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM customers_current WHERE customer_id='C1'"
        ).fetchone()

    assert row["version"] == 2
    assert row["amount"] == 200.0


def test_invalid_and_malformed_records_are_quarantined(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    source = tmp_path / "events.jsonl"

    write_jsonl(source, [
        {"event_id": "bad", "operation": "INSERT"},
        "{not valid json",
    ])

    result = cdc_engine.process_file(source)

    assert result["rejected_count"] == 2
    assert result["reconciliation"]["quarantined_records"] == 2


def test_rerun_is_idempotent(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    source = tmp_path / "events.jsonl"

    write_jsonl(source, [event("e1", "C1", "INSERT", 1)])

    first = cdc_engine.process_file(source)
    second = cdc_engine.process_file(source)

    assert first["applied_count"] == 1
    assert second["applied_count"] == 0
    assert second["duplicate_count"] == 1

    with cdc_engine.get_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM event_history"
        ).fetchone()[0]

    assert count == 1


def test_delete_leaves_tombstone(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    source = tmp_path / "events.jsonl"

    write_jsonl(source, [
        event("e1", "C1", "INSERT", 1),
        event("e2", "C1", "DELETE", 2),
    ])

    result = cdc_engine.process_file(source)

    with cdc_engine.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM customers_current WHERE customer_id='C1'"
        ).fetchone()

    assert row["is_deleted"] == 1
    assert row["version"] == 2
    assert result["reconciliation"]["checks_passed"] is True


def test_empty_file_succeeds(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    source = tmp_path / "empty.jsonl"
    source.write_text("", encoding="utf-8")

    result = cdc_engine.process_file(source)

    assert result["input_count"] == 0
    assert result["applied_count"] == 0


def test_unexpected_schema_field_is_quarantined(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    source = tmp_path / "unexpected_field.jsonl"
    record = event("e1", "C1", "INSERT", 1, unexpected_column="new-source-field")
    write_jsonl(source, [record])

    result = cdc_engine.process_file(source)

    assert result["rejected_count"] == 1
    with cdc_engine.get_connection() as conn:
        row = conn.execute(
            "SELECT error_reason, raw_record FROM quarantine_events"
        ).fetchone()
    assert "Unexpected fields" in row["error_reason"]
    assert "unexpected_column" in row["raw_record"]


def test_null_required_business_field_is_quarantined(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    source = tmp_path / "null_amount.jsonl"
    write_jsonl(source, [event("e1", "C1", "INSERT", 1, amount=None)])

    result = cdc_engine.process_file(source)

    assert result["rejected_count"] == 1
    assert result["reconciliation"]["quarantined_records"] == 1


def test_conflicting_duplicate_event_id_is_not_applied_twice(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    source = tmp_path / "conflicting_duplicate.jsonl"
    first = event("same-id", "C1", "INSERT", 1, amount=100.0)
    conflicting = event("same-id", "C1", "INSERT", 1, amount=999.0)
    write_jsonl(source, [first, conflicting])

    result = cdc_engine.process_file(source)

    assert result["applied_count"] == 1
    assert result["duplicate_count"] == 1
    with cdc_engine.get_connection() as conn:
        row = conn.execute(
            "SELECT amount FROM customers_current WHERE customer_id='C1'"
        ).fetchone()
    assert row["amount"] == 100.0


def test_invalid_operation_is_quarantined(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    source = tmp_path / "invalid_operation.jsonl"
    write_jsonl(source, [event("e1", "C1", "UPSERT", 1)])

    result = cdc_engine.process_file(source)

    assert result["rejected_count"] == 1
    assert result["reconciliation"]["checks_passed"] is True


def test_negative_amount_is_quarantined(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    source = tmp_path / "negative_amount.jsonl"
    write_jsonl(source, [event("e1", "C1", "INSERT", 1, amount=-0.01)])

    result = cdc_engine.process_file(source)

    assert result["rejected_count"] == 1
    assert result["reconciliation"]["quarantined_records"] == 1


def test_partial_failure_rolls_back_data_and_records_failed_run(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    source = tmp_path / "retry.jsonl"
    write_jsonl(source, [event("e1", "C1", "INSERT", 1)])

    original_apply = cdc_engine._apply_event

    def fail_once(*args, **kwargs):
        raise RuntimeError("simulated write failure")

    monkeypatch.setattr(cdc_engine, "_apply_event", fail_once)
    try:
        cdc_engine.process_file(source)
        assert False, "process_file should raise after simulated failure"
    except RuntimeError as exc:
        assert "simulated write failure" in str(exc)

    with cdc_engine.get_connection() as conn:
        run = conn.execute(
            "SELECT status, error_message FROM pipeline_runs"
        ).fetchone()
        customer_count = conn.execute(
            "SELECT COUNT(*) FROM customers_current"
        ).fetchone()[0]
    assert run["status"] == "FAILED"
    assert "simulated write failure" in run["error_message"]
    assert customer_count == 0

    monkeypatch.setattr(cdc_engine, "_apply_event", original_apply)
    retry = cdc_engine.process_file(source)
    assert retry["status"] == "SUCCEEDED"
    assert retry["applied_count"] == 1
