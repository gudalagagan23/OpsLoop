# Billing runbook (TaskFlow, simulated)

## Payment provider timeouts and failing webhooks
If `billing_payment_total{result="timeout"}` rises, first check whether the provider itself is down
(provider status, all tenants affected equally). A provider-side outage cannot be fixed in code:
escalate to a human with the evidence. If only some requests time out, set explicit client timeouts,
retry with backoff, and make webhook handlers idempotent.

## Duplicate charges
Retrying a charge without an idempotency key creates duplicates. Send the same idempotency key on
every retry of one logical charge and record processed keys.
