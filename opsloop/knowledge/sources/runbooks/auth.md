# Auth runbook (TaskFlow, simulated)

## 401 spike after a configuration change
If `auth_token_errors_total` or 401 responses jump right after a deploy or config change, compare
the JWT secret and expiry (`TASKFLOW_JWT_SECRET`, `TASKFLOW_JWT_TTL_S`) between the issuing and
verifying code paths. Tokens signed with one secret and verified with another fail every request.
Revert the configuration change; do not weaken verification to make errors disappear.

## Login attempt burst
A burst of `auth_login_total{result="failure"}` across many emails from few sources is credential
stuffing. Add per-source rate limiting on `/auth/login`. Never disable authentication checks.
