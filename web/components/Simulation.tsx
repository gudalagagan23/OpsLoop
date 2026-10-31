"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { postJson, WING_LABEL, type Fault } from "@/lib/api";
import { useAuth } from "./Auth";
import { Skeleton } from "./CommandCenter";
import { Icon } from "./Icon";
import { useApi } from "./useApi";

const EXPECT: Record<string, string> = {
  "1": "API p95 latency and DB queries per request climb on the task list route. Expect an S2 incident in the API and Database wings within about a minute.",
  "3": "A hot query loses its index and slows down. Expect a slow-query incident in the Database wing.",
  "4": "Connections are borrowed and never returned. The pool fills, requests time out, and an S1 incident opens.",
};

export function Simulation() {
  const { data, error, reload } = useApi<Fault[]>("/simulation/faults", 5000);
  const { me, token } = useAuth();
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const canAct = me?.role === "approver";

  const act = async (path: string, label: string) => {
    setBusy(label);
    setMsg(null);
    try {
      const res = await postJson<{ incident_id?: number }>(path, {}, token);
      if (res.incident_id) {
        router.push(`/incidents/${res.incident_id}`);
        return;
      }
      setMsg({ ok: true, text: `${label}: done. Watch the Command Center and the corner pop-ups.` });
      reload();
    } catch (e) {
      setMsg({ ok: false, text: `${label}: ${(e as Error).message}` });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Simulation Lab</h1>
          <p className="muted">
            TaskFlow ships with real bugs behind feature flags. Turn one on and watch OpsLoop detect,
            investigate and propose a fix on its own, then stop for your approval.
          </p>
        </div>
        <button
          className="btn"
          disabled={!canAct || busy !== null}
          onClick={() => act("/simulation/reset", "Reset all faults")}
        >
          <Icon name="refresh" /> Reset all faults
        </button>
      </header>

      {!canAct && (
        <p className="banner warn">
          <Icon name="lock" size={20} /> Only an approver can change the simulation. Use <strong>Sign in</strong> at the
          top right with an approver token.
        </p>
      )}
      {msg && <p className={`banner ${msg.ok ? "good" : "bad"}`}>{msg.text}</p>}
      {error && !data && <p className="banner bad">Fault control is unavailable: {error}</p>}
      {!data && !error && <Skeleton h={180} />}

      <div className="fault-grid">
        {data?.map((f) => (
          <article key={f.id} className={`fault-card ${f.on ? "on" : ""}`}>
            <header>
              <span className="tag">Fault {f.id}</span>
              <span className="tag">{WING_LABEL[f.wing] ?? f.wing}</span>
              <span className={`pill ${f.on ? "bad" : "ok"}`}>{f.on ? "injected" : "off"}</span>
            </header>
            <h3>{f.title}</h3>
            <p className="muted small">{EXPECT[f.id] ?? "Watch the board for a new incident."}</p>
            <button
              className={`btn ${f.on ? "" : "reject"}`}
              disabled={!canAct || busy !== null}
              onClick={() => act(`/simulation/faults/${f.id}/${f.on ? "off" : "on"}`, `${f.on ? "Turn off" : "Inject"} fault ${f.id}`)}
            >
              <Icon name={f.on ? "stop" : "play"} /> {f.on ? "Turn off" : "Inject fault"}
            </button>
          </article>
        ))}
      </div>

      <section className="panel drill">
        <div className="panel-head">
          <h2>
            <Icon name="rocket" /> Bad-release drill
          </h2>
          <button
            className="btn reject"
            disabled={!canAct || busy !== null}
            onClick={() => act("/simulation/bad-release-drill", "Bad-release drill")}
          >
            <Icon name="play" /> Ship a known-bad build
          </button>
        </div>
        <p className="muted small">
          Releases a build that breaks task creation (POST /tasks returns 500) through the real staged
          rollout. Watch it go to 5%, then 25%, and get rolled back automatically when its error rate
          rises above stable. The Judge records UNSUCCESSFUL. Takes about two minutes. You are recorded
          as the approver of the drill.
        </p>
      </section>

      <section className="panel">
        <h2>
          <Icon name="spark" /> What happens after you inject a fault
        </h2>
        <ol className="steps-list">
          <li>The watchers see the metric cross its threshold and fire a signal.</li>
          <li>The Incident Manager opens or joins an incident, scores severity and posts a pop-up.</li>
          <li>The Investigator reads metrics, logs, the dependency graph and the knowledge base, then writes Report 1.</li>
          <li>The coding agent picks a strategy and hands it to the sector specialist, inside a sandbox.</li>
          <li>The Test Loop replays the fault and runs visible and hidden tests, then writes Report 2.</li>
          <li>
            You get an <strong>Approval needed</strong> pop-up. Review the diff and evidence on the{" "}
            <Link href="/approvals" className="link">
              Approvals
            </Link>{" "}
            page.
          </li>
          <li>
            Approve it: the fix goes 5% → 25% → 100% of traffic, is promoted, and the Judge gives its
            verdict. A RESOLVED fix is added to the knowledge base by the Learner.
          </li>
        </ol>
        <p className="muted small">
          Tip: close any open incident first (approve or reject it). A new fault in a related service is
          correlated into an incident that is still open instead of opening a new one.
        </p>
      </section>
    </div>
  );
}
