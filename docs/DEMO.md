# Running OpsLoop and the live demo

OpsLoop does autonomous investigation and fix proposal, with a human-gated release. Everything it
monitors (TaskFlow, its tenants, traffic and faults) is simulated.

- Public dashboard: <https://ops-loop.vercel.app>
- Backend: runs on this laptop (Docker + a worker), reached through a Cloudflare quick tunnel.
  The dashboard works whenever the laptop is on and OpsLoop is started. When it isn't, the
  dashboard says the backend is offline and reconnects by itself once it's back.

## Start it (one click)

Use any of these. They all run `scripts/start-opsloop.ps1`:

- Desktop folder **OpsLoop** → **Start OpsLoop**, or
- Desktop folder **OpsLoop** → **Open OpsLoop in VS Code**. VS Code runs it when the folder opens
  (the first time, VS Code asks whether to allow automatic tasks: choose **Allow**), or
- double-click `Start-OpsLoop.cmd` in the project folder.

The script:

1. starts Docker Desktop if needed, then the stack (`docker compose up -d --no-build`);
2. starts the worker (log: `logs/worker.log`);
3. opens a tunnel to the API on port 8000 only (the fault-control port stays private) and
   publishes its address to the `backend-url` branch of the GitHub repo. The Vercel dashboard
   reads that address when it loads, so nothing needs redeploying or pasting;
4. keeps watching: it restarts the stack, the worker or the tunnel if one stops, and keeps the
   laptop awake while its window is open.

Leave its window open. Closing it stops the watching. To stop everything: `docker compose down`.

Docker Desktop on this laptop sometimes fails to start because it cannot remove socket files
from its previous run (its error dialog offers "Reset to factory defaults": don't). The start
script handles this: it moves those folders aside before starting Docker, and if Docker's
backend still crashes it quits Docker and retries once. Don't accept a Docker Desktop
"Update and restart" prompt right before presenting: the restart stops every container.

If publishing fails (no git credentials), the window prints the tunnel address. Paste it into the
dashboard under **Sign in → OpsLoop API address → Test & connect**.

## Sign in

Click **Sign in** and paste the approver token from `OPSLOOP_API_TOKENS` in `.env`. Approvers can
approve fixes and drive the Simulation Lab; viewers can only look.

## Demo flow

1. **Command Center**: KPIs, the nine-stage loop, live system behaviour, departments, activity.
2. **Close open incidents first** (approve or reject them). A new fault in a related service is
   correlated into an incident that is still open instead of opening a new one.
3. **Simulation Lab**: inject fault 4 (connection leak) or fault 1 (N+1 query).
4. Within about a minute a **New S1 incident** pop-up appears and the Database wing turns red.
5. The Investigator writes Report 1 (root cause, confidence, cited evidence). The coding agent and
   the database specialist write a fix in the sandbox, and the Test Loop validates it (about two
   to three minutes).
6. An **Approval needed** pop-up appears. Click **Review now**, read the diff and Report 2, then
   **Approve this version**. The approval is bound to that exact version hash.
7. The release starts by itself: pop-ups and the incident page follow it 5% → 25% → 100% of
   traffic (about three minutes). Promotion is simulated: stable switches the planted fault off,
   which is exactly the code path the approved patch removes.
8. The **Judge** compares the fix against stable, measured concurrently, and gives a verdict
   (RESOLVED, PARTIAL, UNSUCCESSFUL or INCONCLUSIVE). A RESOLVED fix is added to the knowledge
   base by the **Learner**.
9. **Download report** on the incident page saves the whole story as Markdown.
10. **Bad-release drill** (Simulation Lab): ships a build that breaks task creation and shows the
    rollout catching it and rolling back automatically. The Judge records UNSUCCESSFUL.
11. **Evidence Ledger**: **Verify chain now** recomputes the hash chain.

## Model quota

The models run on the Gemini free tier. Its daily quota resets at midnight Pacific time
(12:30 IST). The worker falls back through `OPSLOOP_LLM_FALLBACK_MODELS` when a model is
overloaded (503) or out of quota (429). If every model is out of quota, the coding agent stops,
says why in the attempt notes, and the incident escalates to a human: the designed safe failure.
A key from a new Google Cloud project, or billing on this one, removes the limit.

## Always on

The backend lives on this laptop, so the public dashboard is live while the laptop is on, awake
and online with OpsLoop started. For true 24/7 the backend would need a server (a small VM with
Docker); the start script works there unchanged.
