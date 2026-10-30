# Running the live demo

OpsLoop does autonomous investigation and fix proposal, with a human-gated release. Everything it
monitors (TaskFlow, its tenants, traffic and faults) is simulated.

The public dashboard is <https://ops-loop.vercel.app>. It talks to the OpsLoop API on this laptop
through a Cloudflare quick tunnel, so the laptop must stay on, awake and online during the demo.

## 1. Start the backend (about 2 minutes)

1. Start Docker Desktop and wait until it says it is running.
2. In a terminal in the `OpsLoop` folder:

   ```bash
   docker compose up -d --no-build
   ```

   `--no-build` reuses the images already on this laptop. The local
   `docker-compose.override.yml` mounts the current source into them.
3. Start the worker (investigations, fixes and validation run here):

   ```bash
   .venv/Scripts/python -m opsloop.worker
   ```

4. Check it: <http://localhost:8000/health> should say `"status": "ok"`.

## 2. Open the tunnel

In PowerShell (keep this window open):

```powershell
& "C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --url http://localhost:8000
```

Copy the `https://….trycloudflare.com` address it prints. Only port 8000 (the API) goes through
the tunnel. The fault-control port 9100 stays private.

## 3. Point the dashboard at the tunnel

A quick tunnel gets a new address every time it starts. You don't need to redeploy:

1. Open <https://ops-loop.vercel.app>.
2. Click **Sign in** (top right), paste the new address under **OpsLoop API address**, and click
   **Test & connect**.
3. Sign in again with the approver token from `.env` (`OPSLOOP_API_TOKENS`).

The top bar should show **Live**, **Watcher health check: 2/2 reporting** and **Ledger verified**.

## 4. Demo flow

1. **Command Center**: KPIs, the nine-stage loop, live system behaviour, departments, activity.
2. **Simulation Lab**: inject fault 4 (connection leak) or fault 1 (N+1 query).
3. Within about a minute a **New S1 incident** pop-up appears and the Database wing turns red.
4. The Investigator writes Report 1 (root cause, confidence, cited evidence). Then the coding agent
   and the database specialist write a fix in the sandbox, and the Test Loop validates it.
5. An **Approval needed** pop-up appears. Click **Review now**, read the diff and Report 2, then
   **Approve this version**. The approval is bound to that exact version hash.
6. On the incident page, **Download report** saves the full incident report as Markdown.
7. **Evidence Ledger**: **Verify chain now** recomputes the hash chain.
8. Reset the fault in the Simulation Lab afterwards.

## Model quota

The models run on the Gemini free tier. Its daily quota resets at midnight Pacific time
(12:30 IST). The worker falls back through the models in `OPSLOOP_LLM_FALLBACK_MODELS` when one is
overloaded (503) or out of quota (429). If every model is out of quota, the coding agent stops,
says so in the attempt notes, and the incident escalates to a human. That is the designed safe
failure. A key from a new Google Cloud project, or billing on this one, removes the limit.
