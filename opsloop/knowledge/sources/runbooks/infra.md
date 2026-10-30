# Infrastructure runbook (TaskFlow, simulated)

## Job queue backlog
`queue_depth` growing while `job_duration_seconds` is normal means the worker concurrency is too
low for the arrival rate. Raise worker concurrency (a configuration change) and confirm the queue
drains.

## Runaway job
`worker_cpu_ratio` pinned near 1.0 with one job running far longer than its usual
`job_duration_seconds` is a runaway job. Add a timeout or resource limit to the job.
