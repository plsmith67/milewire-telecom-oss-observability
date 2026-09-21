"""Correlate Alertmanager alarms into site/sector incidents."""

from __future__ import annotations

from typing import Any

from app.store import IncidentStore, utc_now

SEVERITY_RANK = {"warning": 1, "major": 2, "critical": 3}

ALERT_DOMAIN = {
    "PLTECellOutage": "RAN",
    "PLTELowAvailability": "RAN",
    "PLTEPoorRSRP": "RF",
    "PLTEPoorRSRQ": "RF",
    "PLTELowSINR": "RF",
    "PLTEHighLatency": "Transport",
    "PLTEHighPacketLoss": "Transport",
    "PLTEThroughputDegradation": "Capacity",
    "PLTEHandoverDegradation": "Mobility",
    "PLTECapacityCongestion": "Capacity",
}


def _domain_for(alarm: dict[str, Any]) -> str:
    if alarm.get("domain"):
        return str(alarm["domain"])
    return ALERT_DOMAIN.get(alarm["alertname"], "RAN")


def _max_severity(severities: list[str]) -> str:
    return max(severities, key=lambda item: SEVERITY_RANK[item])


def _domain_of(domains: list[str]) -> str:
    unique = {item for item in domains if item}
    if len(unique) > 1:
        return "Mixed"
    if len(unique) == 1:
        return next(iter(unique))
    return "RAN"


class Correlator:
    def __init__(self, store: IncidentStore) -> None:
        self.store = store

    def ingest(self, alarms: list[dict[str, Any]]) -> list[str]:
        ordered = sorted(alarms, key=lambda item: item.get("starts_at") or "")
        touched: list[str] = []
        with self.store._lock:
            for alarm in ordered:
                incident_id = self._apply(alarm)
                if incident_id and incident_id not in touched:
                    touched.append(incident_id)
            self.store.commit()
        return touched

    def _apply(self, alarm: dict[str, Any]) -> str:
        existing = self.store.get_alarm_by_fingerprint(alarm["fingerprint"])
        open_incident = self.store.find_open(alarm["site"], alarm["sector"])

        if existing is not None:
            current_incident = self.store.get_incident(existing["incident_id"])
            still_open = (
                current_incident is not None
                and current_incident["lifecycle_state"] in {"active", "acknowledged"}
            )
            if still_open:
                previous_status = existing["status"]
                self.store.update_alarm(alarm["fingerprint"], alarm)
                if previous_status == alarm["status"] and alarm["status"] == "firing":
                    self._recompute(existing["incident_id"], changed=False)
                else:
                    if alarm["status"] == "resolved" and previous_status != "resolved":
                        self.store.add_event(
                            existing["incident_id"],
                            "alarm_resolved",
                            {"fingerprint": alarm["fingerprint"], "alertname": alarm["alertname"]},
                        )
                    self._recompute(existing["incident_id"], changed=True)
                return existing["incident_id"]

            if alarm["status"] == "resolved":
                self.store.update_alarm(alarm["fingerprint"], alarm)
                self.store.commit()
                return existing["incident_id"]

        incident_id = open_incident["id"] if open_incident is not None else None
        created = False
        if incident_id is None and alarm["status"] == "firing":
            incident_id = self.store.insert_incident(
                site=alarm["site"],
                sector=alarm["sector"],
                severity=alarm["severity"],
                probable_domain=_domain_for(alarm),
                first_detected=alarm.get("starts_at") or utc_now(),
            )
            created = True
        if incident_id is None:
            # Resolved alert with no open incident is ignored.
            self.store.commit()
            return ""

        if existing is None:
            self.store.insert_alarm(incident_id, alarm)
            if not created:
                self.store.add_event(
                    incident_id,
                    "alarm_added",
                    {
                        "fingerprint": alarm["fingerprint"],
                        "alertname": alarm["alertname"],
                        "severity": alarm["severity"],
                    },
                )
        else:
            self.store.update_alarm(alarm["fingerprint"], alarm, incident_id=incident_id)
            self.store.add_event(
                incident_id,
                "alarm_added",
                {"fingerprint": alarm["fingerprint"], "alertname": alarm["alertname"], "reopened": True},
            )
        self._recompute(incident_id, changed=not created)
        return incident_id

    def _recompute(self, incident_id: str, *, changed: bool) -> None:
        if not incident_id:
            return
        incident = self.store.get_incident(incident_id)
        if incident is None:
            return
        alarms = self.store.list_alarms(incident_id)
        firing = [row for row in alarms if row["status"] == "firing"]
        import json

        domains = []
        for row in alarms:
            labels = json.loads(row["labels_json"])
            domains.append(str(labels.get("domain") or ALERT_DOMAIN.get(row["alertname"], "RAN")))
        domain = _domain_of(domains)
        if not firing:
            if incident["lifecycle_state"] != "cleared":
                self.store.touch(
                    incident_id,
                    lifecycle_state="cleared",
                    cleared_at=utc_now(),
                    probable_domain=domain,
                )
                self.store.add_event(incident_id, "cleared", {"reason": "all contributing alarms resolved"})
            return

        severities = [row["severity"] for row in firing]
        severity = _max_severity(severities)
        if severity != incident["severity"]:
            self.store.add_event(
                incident_id,
                "severity_changed",
                {"from": incident["severity"], "to": severity},
            )
        if changed or severity != incident["severity"] or domain != incident["probable_domain"]:
            self.store.touch(incident_id, severity=severity, probable_domain=domain)
        else:
            self.store.touch(incident_id)

    def acknowledge(self, incident_id: str, acknowledged_by: str) -> str:
        with self.store._lock:
            incident = self.store.get_incident(incident_id)
            if incident is None:
                return "missing"
            if incident["lifecycle_state"] == "cleared":
                return "cleared"
            if incident["lifecycle_state"] == "acknowledged":
                return "already"
            self.store.touch(
                incident_id,
                lifecycle_state="acknowledged",
                acknowledged_at=utc_now(),
                acknowledged_by=acknowledged_by,
            )
            self.store.add_event(
                incident_id,
                "acknowledged",
                {"acknowledged_by": acknowledged_by},
            )
            self.store.commit()
            return "ok"
