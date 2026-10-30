# API runbook (TaskFlow, simulated)

## High API p95 latency on a route
Check `db_queries_per_request` for the route first. A p95 above 10 queries per request on a list
endpoint almost always means an N+1 loop: one query for the list, then one or more per row.
Look at the handler for per-row `conn.execute` calls inside a `for` loop. Fix by loading the
related data in the list query (JOIN or a correlated subquery) so one request issues one or two
queries. Confirm with `http_request_duration_seconds` and `db_queries_per_request` after the fix.
If query counts are normal but latency is high, check `db_query_duration_seconds` by `query_name`.

## API 5xx error rate high
Find which routes return 5xx (`http_requests_total{status=~"5.."}` by `route`). If errors started
within minutes of a deploy (see `deploy_events`), a rollback to the previous version is the
fastest safe action. If every route fails together and `db_pool_timeouts_total` is rising, this is
a database pool problem, not an API bug: see the database runbook.

## Request rate drop
A sudden fall in `http_requests_total` rate with no errors usually means upstream traffic stopped
(client outage, DNS) rather than an application fault. Escalate to a human; do not change code.
