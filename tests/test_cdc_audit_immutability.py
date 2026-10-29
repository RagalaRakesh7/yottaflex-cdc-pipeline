
import sqlite3

import pytest
import cdc_engine
from test_cdc_engine import setup_test_db, event, write_jsonl


def test_audit_history_cannot_be_updated_or_deleted(
    monkeypatch, tmp_path
):
    setup_test_db(monkeypatch, tmp_path)
    source = tmp_path / "audit_immutability.jsonl"
    write_jsonl(source, [event("audit-1", "C1", "INSERT", 1)])

    result = cdc_engine.process_file(source)
    assert result["applied_count"] == 1

    with cdc_engine.get_connection() as conn:
        with pytest.raises(
            sqlite3.IntegrityError, match="Audit history is immutable"
        ):
            conn.execute(
                "UPDATE event_history SET outcome = 'CHANGED' "
                "WHERE event_id = ?",
                ("audit-1",),
            )

        with pytest.raises(
            sqlite3.IntegrityError, match="Audit history is immutable"
        ):
            conn.execute(
                "DELETE FROM event_history WHERE event_id = ?",
                ("audit-1",),
            )

        count = conn.execute(
            "SELECT COUNT(*) FROM event_history WHERE event_id = ?",
            ("audit-1",),
        ).fetchone()[0]

    assert count == 1
