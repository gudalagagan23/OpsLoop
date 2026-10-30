# Database runbook (TaskFlow, simulated)

## Slow query on a table with a filter or join
A query whose `db_query_duration_seconds` p95 rises from a few ms to tens of ms while the query
count per request stays normal points at a missing or unusable index. Run `EXPLAIN` on the query.
A `Seq Scan` on `taskflow.tasks` filtered by `project_id` and `status` means the composite index
`ix_tasks_project_status (project_id, status)` is missing. Fix with a migration/bootstrap step that
creates the index (`CREATE INDEX IF NOT EXISTS`). Index creation is a schema change and needs
human approval.

## Connection pool exhausted
Symptoms: `db_pool_in_use` equals `db_pool_size`, `db_pool_timeouts_total` rising, HTTP 503 and
requests stalling for the pool timeout (3 s), and p95 latency near that timeout on every route.
Cause is usually connections that are checked out and never returned: a code path that calls
`engine.connect()` without a `with` block or `finally: close()`, or a long-held transaction.
Fix the leak so every connection is released. Raising the pool size only delays the failure and is
not a fix by itself. Confirm with zero pool timeouts under load.

## Lock contention
`db_lock_wait_seconds` rising together with slow writes points to a long transaction holding row
locks. Find transactions that do external work between BEGIN and COMMIT and shorten them.
