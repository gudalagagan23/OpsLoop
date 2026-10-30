"""Incident Manager: dedupe signals, correlate them into incidents, compute severity, own the
lifecycle. Every change writes an incident_events row and a ledger entry. The caller commits."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from opsloop.core import ledger
from opsloop.core.models import DeployEvent, Incident, IncidentEvent, ServiceDependency, Signal
from opsloop.incidents.severity import SeverityInputs, compute_severity, worst
from opsloop.incidents.state_machine import ACTIVE_STATES, TERMINAL_STATES, check_transition

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class FiredSignal:
    watcher: str  # also the wing: api, database, ...
    signal_id: str
    series_key: str
    labels: dict[str, str]
    service: str
    severity: str
    method: str
    query: str
    value: float
    baseline: float | None
    condition_since: datetime
    fired_at: datetime
    title: str
    extra: dict[str, Any] = field(default_factory=dict)


class IncidentManager:
    def __init__(
        self,
        session: Session,
        *,
        correlation_window_s: float = 300,
        retriage_interval_s: float = 60,
        run_tag: str | None = None,
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        self.s = session
        self.window = timedelta(seconds=correlation_window_s)
        self.retriage_interval = timedelta(seconds=retriage_interval_s)
        self.run_tag = run_tag
        self.now = clock

    # ---- signals -------------------------------------------------------------------------

    def ingest(self, fired: FiredSignal) -> Incident:
        """Record a firing signal. Repeats of an open signal are deduplicated."""
        existing = self.s.execute(
            select(Signal).where(
                Signal.signal_id == fired.signal_id,
                Signal.series_key == fired.series_key,
                Signal.cleared_at.is_(None),
            )
        ).scalar_one_or_none()
        if existing is not None and existing.incident_id is not None:
            owner = self.s.get_one(Incident, existing.incident_id)
            if owner.state not in TERMINAL_STATES:
                return self._update_open_signal(existing, fired)
            # The incident was closed while this signal still fired: retire the old signal
            # row and treat this firing as new, so it can't hide inside a closed incident.
            existing.cleared_at = fired.fired_at

        incident = self._correlate(fired.service)
        opened = incident is None
        if incident is None:
            incident = Incident(
                title=fired.title,
                wing=fired.watcher,
                service=fired.service,
                severity=fired.severity,
                state="OPEN",
                run_tag=self.run_tag,
                first_signal_at=fired.fired_at,
                last_signal_at=fired.fired_at,
                signal_count=0,
            )
            self.s.add(incident)
            self.s.flush()

        ev = ledger.append(
            self.s,
            type="observation",
            source=f"watcher:{fired.watcher}",
            incident_id=incident.id,
            payload={
                "signal_id": fired.signal_id,
                "labels": fired.labels,
                "service": fired.service,
                "severity": fired.severity,
                "method": fired.method,
                "query": fired.query,
                "value": fired.value,
                "baseline": fired.baseline,
                "condition_since": fired.condition_since.isoformat(),
                "fired_at": fired.fired_at.isoformat(),
                **fired.extra,
            },
        )
        self.s.add(
            Signal(
                watcher=fired.watcher,
                signal_id=fired.signal_id,
                series_key=fired.series_key,
                labels=fired.labels,
                service=fired.service,
                severity=fired.severity,
                method=fired.method,
                query=fired.query,
                value=fired.value,
                baseline=fired.baseline,
                condition_since=fired.condition_since,
                fired_at=fired.fired_at,
                last_value_at=fired.fired_at,
                incident_id=incident.id,
                evidence_id=ev.id,
            )
        )
        incident.signal_count += 1
        incident.last_signal_at = max(incident.last_signal_at, fired.fired_at)
        incident.updated_at = self.now()
        self._event(
            incident,
            "opened" if opened else "signal_attached",
            actor="incident-manager",
            reason=fired.title,
            evidence_id=ev.id,
        )
        self.triage(incident)
        return incident

    def _update_open_signal(self, sig: Signal, fired: FiredSignal) -> Incident:
        incident = self.s.get_one(Incident, sig.incident_id)
        sig.value = fired.value
        sig.baseline = fired.baseline
        sig.last_value_at = fired.fired_at
        incident.last_signal_at = max(incident.last_signal_at, fired.fired_at)
        escalated = worst(sig.severity, fired.severity) != sig.severity
        if escalated:
            ev = ledger.append(
                self.s,
                type="observation",
                source=f"watcher:{fired.watcher}",
                incident_id=incident.id,
                payload={
                    "signal_id": fired.signal_id,
                    "labels": fired.labels,
                    "severity_change": [sig.severity, fired.severity],
                    "value": fired.value,
                    "baseline": fired.baseline,
                },
            )
            sig.severity = fired.severity
            self._event(incident, "signal_escalated", "incident-manager", fired.title, ev.id)
        if escalated or self.now() - incident.updated_at >= self.retriage_interval:
            self.triage(incident)
        return incident

    def clear(self, signal_id: str, series_key: str, at: datetime) -> None:
        sig = self.s.execute(
            select(Signal).where(
                Signal.signal_id == signal_id,
                Signal.series_key == series_key,
                Signal.cleared_at.is_(None),
            )
        ).scalar_one_or_none()
        if sig is None:
            return
        sig.cleared_at = at
        ledger.append(
            self.s,
            type="observation",
            source=f"watcher:{sig.watcher}",
            incident_id=sig.incident_id,
            payload={"signal_id": signal_id, "labels": sig.labels, "cleared_at": at.isoformat()},
        )

    def _correlate(self, service: str) -> Incident | None:
        related = {service} | self._neighbours(service)
        q = select(Incident).where(
            Incident.state.in_(ACTIVE_STATES),
            Incident.service.in_(related),
            Incident.last_signal_at >= self.now() - self.window,
        )
        if self.run_tag is not None:  # replay runs never correlate across runs
            q = q.where(Incident.run_tag == self.run_tag)
        return self.s.execute(
            q.order_by(Incident.last_signal_at.desc()).limit(1)
        ).scalar_one_or_none()

    # ---- dependency graph ----------------------------------------------------------------

    def _edges(self) -> list[tuple[str, str]]:
        rows = self.s.execute(select(ServiceDependency.service, ServiceDependency.depends_on))
        return [(a, b) for a, b in rows]

    def _neighbours(self, service: str) -> set[str]:
        out: set[str] = set()
        for a, b in self._edges():
            if a == service:
                out.add(b)
            if b == service:
                out.add(a)
        return out

    def blast_radius(self, services: set[str]) -> int:
        """Number of other services that (transitively) depend on any of `services`."""
        dependents: dict[str, set[str]] = {}
        for a, b in self._edges():
            dependents.setdefault(b, set()).add(a)
        seen: set[str] = set()
        stack = list(services)
        while stack:
            for dep in dependents.get(stack.pop(), set()):
                if dep not in seen and dep not in services:
                    seen.add(dep)
                    stack.append(dep)
        return len(seen)

    # ---- triage --------------------------------------------------------------------------

    def triage(self, incident: Incident) -> None:
        signals = (
            self.s.execute(select(Signal).where(Signal.incident_id == incident.id)).scalars().all()
        )
        open_sigs = [s for s in signals if s.cleared_at is None] or list(signals)
        signal_sev = min((s.severity for s in open_sigs), default=incident.severity)
        since = incident.first_signal_at - timedelta(minutes=2)
        tenant_rows = self.s.execute(
            text(
                "SELECT COUNT(DISTINCT tenant_id), "
                "COALESCE(array_agg(DISTINCT attrs->>'plan') "
                "FILTER (WHERE attrs ? 'plan'), '{}') "
                "FROM ops.app_logs WHERE ts >= :since AND level IN ('ERROR', 'WARNING') "
                "AND tenant_id IS NOT NULL"
            ),
            {"since": since},
        ).one()
        tenants, plans = int(tenant_rows[0]), frozenset(p for p in tenant_rows[1] if p)
        error_rate = max(
            (s.value for s in open_sigs if s.signal_id.endswith("error_rate")), default=0.0
        )
        inputs = SeverityInputs(
            signal_severity=signal_sev,
            tenants_affected=tenants,
            plans_affected=plans,
            error_rate=error_rate,
            duration_s=(self.now() - incident.first_signal_at).total_seconds(),
            blast_radius=self.blast_radius({s.service for s in signals}),
        )
        result = compute_severity(inputs)
        first_triage = incident.state == "OPEN"
        # Severity only rises while an incident is active; it is never quietly downgraded.
        new_sev = worst(result.severity, incident.severity) if not first_triage else result.severity
        incident.updated_at = self.now()
        if first_triage or new_sev != incident.severity:
            ev = ledger.append(
                self.s,
                type="action",
                source="incident-manager",
                incident_id=incident.id,
                payload={
                    "action": "triage",
                    "severity": new_sev,
                    "previous": None if first_triage else incident.severity,
                    "points": result.points,
                    "breakdown": result.breakdown,
                    "inputs": {
                        "signal_severity": inputs.signal_severity,
                        "tenants_affected": inputs.tenants_affected,
                        "plans_affected": sorted(inputs.plans_affected),
                        "error_rate": inputs.error_rate,
                        "duration_s": round(inputs.duration_s, 1),
                        "blast_radius": inputs.blast_radius,
                    },
                },
            )
            if not first_triage:
                self._event(incident, "severity_changed", "incident-manager", new_sev, ev.id)
            incident.severity = new_sev
        if first_triage:
            self._record_deploy_correlation(incident)
            self.transition(incident, "TRIAGED", actor="incident-manager", reason="severity set")

    def _record_deploy_correlation(self, incident: Incident) -> None:
        deploys = (
            self.s.execute(
                select(DeployEvent)
                .where(
                    DeployEvent.ts >= incident.first_signal_at - timedelta(minutes=30),
                    DeployEvent.ts <= incident.first_signal_at,
                )
                .order_by(DeployEvent.ts.desc())
                .limit(3)
            )
            .scalars()
            .all()
        )
        for d in deploys:
            ledger.append(
                self.s,
                type="deploy_event",
                source="incident-manager",
                incident_id=incident.id,
                payload={
                    "deploy_event_id": d.id,
                    "service": d.service,
                    "version": d.version,
                    "git_sha": d.git_sha,
                    "stage": d.stage,
                    "ts": d.ts.isoformat(),
                    "seconds_before_first_signal": (
                        incident.first_signal_at - d.ts
                    ).total_seconds(),
                },
            )

    # ---- lifecycle -----------------------------------------------------------------------

    def transition(
        self, incident: Incident, to_state: str, *, actor: str, reason: str | None = None
    ) -> None:
        check_transition(incident.state, to_state)
        ev = ledger.append(
            self.s,
            type="action",
            source=actor,
            incident_id=incident.id,
            payload={
                "action": "transition",
                "from": incident.state,
                "to": to_state,
                "reason": reason,
            },
        )
        self._event(incident, "state_change", actor, reason, ev.id, incident.state, to_state)
        incident.state = to_state
        incident.updated_at = self.now()
        if to_state in TERMINAL_STATES:
            incident.closed_at = self.now()
            incident.closed_reason = reason

    def _event(
        self,
        incident: Incident,
        kind: str,
        actor: str,
        reason: str | None,
        evidence_id: int | None,
        from_state: str | None = None,
        to_state: str | None = None,
    ) -> None:
        self.s.add(
            IncidentEvent(
                incident_id=incident.id,
                ts=self.now(),
                kind=kind,
                from_state=from_state,
                to_state=to_state,
                actor=actor,
                reason=reason,
                evidence_id=evidence_id,
            )
        )
        self.s.flush()


def active_incident_count(session: Session) -> int:
    return session.execute(
        select(func.count()).select_from(Incident).where(Incident.state.in_(ACTIVE_STATES))
    ).scalar_one()
