
import cdc_engine
from test_cdc_engine import setup_test_db, event, write_jsonl


def test_update_for_nonexistent_customer_behavior(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    source = tmp_path / "unknown_customer_update.jsonl"

    write_jsonl(
        source,
        [event("e-unknown-1", "UNKNOWN", "UPDATE", 1)],
    )

    result = cdc_engine.process_file(source)

    with cdc_engine.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM customers_current WHERE customer_id = ?",
            ("UNKNOWN",),
        ).fetchone()

    print("Applied:", result["applied_count"])
    print("Rejected:", result["rejected_count"])
    print("Customer row created:", row is not None)

    # This is a diagnostic test, not an assumption that either
    # upsert or strict INSERT-before-UPDATE is the intended policy.
    assert result["applied_count"] + result["rejected_count"] >= 1
