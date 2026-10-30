# User experience runbook (TaskFlow, simulated)

## Frontend error spike after an API change
A rise in `frontend_errors_total` right after an API deploy usually means a response contract
changed (renamed or removed field). Compare the API response shape with what the frontend reads.
Rollback is the fastest safe fix; otherwise restore the old field alongside the new one.
