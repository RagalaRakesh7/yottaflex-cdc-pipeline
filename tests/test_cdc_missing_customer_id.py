
import cdc_engine
from test_cdc_engine import setup_test_db, write_jsonl


def test_missing_customer_id_is_quarantined(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    source = tmp_path / "missing_customer_id.jsonl"

    write_jsonl(source, [{
        "event_id": "missing-customer-1",
        "operation": "INSERT",
        "version": 1,
        "event_time": "2026-10-29T09:00:00Z",
        "name": "Test Customer",
        "email": "test@example.com",
        "amount": 100.0,
    }])

    result = cdc_engine.process_file(source)

    with cdc_engine.get_connection() as conn:
        customer_count = conn.execute(
            "SELECT COUNT(*) FROM customers_current"
        ).fetchone()[0]
        quarantine = conn.execute(
            """SELECT error_reason, raw_record
               FROM quarantine_events"""
        ).fetchone()

    assert result["rejected_count"] == 1
    assert customer_count == 0
    assert quarantine is not None
    assert "customer_id" in quarantine["error_reason"]
    assert "missing-customer-1" in quarantine["raw_record"]
