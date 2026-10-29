# Design Notes and Trade-offs

## Decisions

1. **SQLite** keeps the solution runnable locally without credentials or external infrastructure. It is simple to inspect and suitable for a focused 72-hour demonstration; it is not a distributed warehouse.
2. **JSON Lines** makes each event independently parseable and lets the pipeline record the source line number for rejects.
3. **Stable `event_id` + `processed_events`** makes reruns idempotent. The first processed occurrence wins if a source reuses an event ID with a different payload; this conflict is counted as a duplicate and is not applied.
4. **Per-customer integer versions** prevent a late older event from overwriting newer state. The pipeline records stale outcomes in the processed registry and increments the stale counter.
5. **Delete tombstones** preserve the customer key and last known version rather than physically deleting current-state evidence.
6. **Quarantine instead of silent dropping** preserves the raw source line and validation reason for investigation and corrected replay.
7. **Append-only history** is protected with SQLite triggers against normal update/delete statements. This is an application-level guard, not a cryptographic integrity guarantee.
8. **Reconciliation** compares each current-state version with the maximum applied history version and reports mismatches.

## Recovery

- Record-level validation and JSON parsing errors are quarantined; the pipeline continues with the next record.
- Unexpected run-level exceptions roll back the active transaction. Investigate the failure, then rerun the input. Already committed earlier runs remain in the database.
- Reprocessing the same successful input is safe for event application because event IDs are checked against the processed registry.
- Backfill should use the same event IDs and version rules. Do not manually edit the immutable history; fix the source and replay corrected events with a clear operational note.

## Limitations and future work

- Add an explicit conflict quarantine path for the same `event_id` arriving with a different payload, rather than counting it only as a duplicate.
- Add a customer reference table if customer IDs must be checked against a separate authoritative source.
- Add an event schema version and configurable compatibility policy if source schema evolution is expected.
- For production, use structured centralized logs, authentication, a production WSGI server, secret management, backups, monitoring and a durable analytical database.
- For high throughput, benchmark before optimization; consider bulk parsing, tuned transaction sizes, and indexes chosen from measured query plans.
