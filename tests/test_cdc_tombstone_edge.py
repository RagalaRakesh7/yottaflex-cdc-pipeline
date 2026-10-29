
import cdc_engine
from test_cdc_engine import setup_test_db, event, write_jsonl


def test_older_update_does_not_resurrect_deleted_customer(
    monkeypatch, tmp_path
):
    setup_test_db(monkeypatch, tmp_path)
    source = tmp_path / "delete_then_old_update.jsonl"

    write_jsonl(source, [
        event("e1", "C1", "INSERT", 1),
        event("e2", "C1", "DELETE", 3),
        event("e3", "C1", "UPDATE", 2),
    ])

    result = cdc_engine.process_file(source)

    with cdc_engine.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM customers_current WHERE customer_id = ?",
            ("C1",),
        ).fetchone()

    assert row is not None
    assert row["is_deleted"] == 1
    assert row["version"] == 3

    print("Applied:", result["applied_count"])
    print("Rejected:", result["rejected_count"])
    print("Customer deleted:", row["is_deleted"] == 1)
    print("Final version:", row["version"])
