"use client";

import Link from "next/link";
import { ago, type Department } from "@/lib/api";
import { statusText, Skeleton } from "./CommandCenter";
import { Icon, WING_ICON } from "./Icon";
import { useApi, useNow } from "./useApi";

const SPECIALIST_LABEL: Record<string, string> = {
  api_specialist: "API specialist",
  database_specialist: "Database specialist",
  auth_specialist: "Auth & security specialist",
  billing_specialist: "Billing specialist",
  infra_specialist: "Infrastructure specialist",
  ux_specialist: "UX specialist",
};

export function Departments() {
  const { data, error } = useApi<Department[]>("/departments", 10000);
  const now = useNow(3000);
  const monitored = data?.filter((d) => d.monitored).length ?? 0;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Departments</h1>
          <p className="muted">
            Six wings of the simulated SaaS. Each has a watcher that detects problems and a specialist
            agent that fixes them. {data ? `${monitored} of ${data.length} are monitored today.` : ""}
          </p>
        </div>
      </header>
      {error && !data && <p className="banner bad">Cannot reach the OpsLoop API: {error}</p>}
      {!data && !error && <Skeleton h={300} />}
      <div className="dept-grid">
        {data?.map((d) => (
          <section key={d.wing} id={d.wing} className={`dept-card st-${d.status}`}>
            <header>
              <span className="dept-icon">
                <Icon name={WING_ICON[d.wing] ?? "info"} size={22} />
              </span>
              <div>
                <h2>{d.name}</h2>
                <p className="muted small">{d.services.join(", ")}</p>
              </div>
              <span className={`status-badge st-${d.status}`}>{statusText(d)}</span>
            </header>
            <p>{d.description}</p>

            <dl className="dept-facts">
              <dt>Watcher</dt>
              <dd>
                {d.watcher ? (
                  <>
                    <span className={`status-dot inline ${now - new Date(d.watcher.last_seen).getTime() > 30000 ? "down" : "ok"}`} />
                    reporting · last seen {ago(d.watcher.last_seen, now)} ago · config v{d.watcher.config_version}
                  </>
                ) : (
                  <span className="muted">not deployed yet{d.planned_stage ? ` (${d.planned_stage})` : ""}</span>
                )}
              </dd>
              <dt>Specialist</dt>
              <dd>{d.specialist ? (SPECIALIST_LABEL[d.specialist] ?? d.specialist) : "–"}</dd>
              <dt>Incidents 24h</dt>
              <dd>{d.incidents_24h}</dd>
            </dl>

            {d.signals.length > 0 && (
              <details className="dept-signals">
                <summary>{d.signals.length} signals watched</summary>
                <ul>
                  {d.signals.map((s) => (
                    <li key={s.id}>
                      <span className="mono">{s.id}</span>
                      <span className="muted small">
                        {" "}
                        · {s.method} · {Object.entries(s.severity).map(([k, v]) => `${k} ${v}`).join(", ")}
                      </span>
                    </li>
                  ))}
                </ul>
              </details>
            )}

            {d.active_incidents.length > 0 && (
              <ul className="dept-incidents">
                {d.active_incidents.map((i) => (
                  <li key={i.id}>
                    <Link href={`/incidents/${i.id}`} className={`mini-card sev-edge-${i.severity}`}>
                      <span className={`sev sev-${i.severity}`}>{i.severity}</span>
                      <span className="mini-title">{i.title}</span>
                      <span className="state">{i.state.replaceAll("_", " ")}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}

            <footer>
              <Link href={`/incidents?wing=${d.wing}`} className="link small">
                View {d.name} incidents →
              </Link>
            </footer>
          </section>
        ))}
      </div>
    </div>
  );
}
