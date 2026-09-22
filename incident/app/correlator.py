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


KPI_DOMAIN = {
    "lte_rsrp_dbm": "RF",
    "lte_rsrq_db": "RF",
    "lte_sinr_db": "RF",
    "lte_dl_throughput_mbps": "Capacity",
    "lte_ul_throughput_mbps": "Capacity",
    "lte_latency_ms": "Transport",
    "lte_packet_loss_pct": "Transport",
    "lte_availability_pct": "RAN",
    "lte_handover_success_pct": "Mobility",
    "lte_active_users": "Capacity",
}


def _anomaly_severity(score: float) -> str:
    if score >= 5.0:
        return "major"
    return "warning"


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

    def ingest_anomalies(self, anomalies: list[dict[str, Any]]) -> list[str]:
        touched: list[str] = []
        with self.store._lock:
            for anomaly in anomalies:
                incident_id = self._apply_anomaly(anomaly)
                if incident_id and incident_id not in touched:
                    touched.append(incident_id)
            self.store.commit()
        return touched

    def _apply_anomaly(self, anomaly: dict[str, Any]) -> str:
        anomaly_id = anomaly["id"]
        site = anomaly["site"]
        sector = anomaly["sector"]
        status = anomaly.get("status") or anomaly.get("lifecycle_state") or "active"
        score = float(anomaly.get("combined_score") or anomaly.get("score") or 0.0)
        domain = anomaly.get("domain") or KPI_DOMAIN.get(anomaly["kpi"], "RAN")
        existing = self.store.get_anomaly(anomaly_id)
        open_incident = self.store.find_open(site, sector)

        if status in {"warming_up"}:
            return existing["incident_id"] if existing else ""

        if existing is not None:
            current_incident = self.store.get_incident(existing["incident_id"])
            still_open = (
                current_incident is not None
                and current_incident["lifecycle_state"] in {"active", "acknowledged"}
            )
            if still_open:
                previous = existing["status"]
                self.store.update_anomaly(anomaly_id, {**anomaly, "status": status, "domain": domain})
                if previous != status and status == "cleared":
                    self.store.add_event(
                        existing["incident_id"],
                        "anomaly_cleared",
                        {"anomaly_id": anomaly_id, "kpi": anomaly["kpi"]},
                    )
                self._recompute(existing["incident_id"], changed=True)
                return existing["incident_id"]
            if status == "cleared":
                self.store.update_anomaly(anomaly_id, {**anomaly, "status": status, "domain": domain})
                self.store.commit()
                return existing["incident_id"]

        if status == "cleared" and existing is None:
            return ""

        if status not in {"active", "recovering"}:
            return ""

        incident_id = open_incident["id"] if open_incident is not None else None
        created = False
        if incident_id is None:
            incident_id = self.store.insert_incident(
                site=site,
                sector=sector,
                severity=_anomaly_severity(score),
                probable_domain=domain,
                first_detected=anomaly.get("first_detected") or anomaly.get("starts_at") or utc_now(),
            )
            created = True

        if existing is None:
            self.store.insert_anomaly(
                incident_id,
                {**anomaly, "status": status, "domain": domain, "combined_score": score},
            )
            if not created:
                self.store.add_event(
                    incident_id,
                    "anomaly_added",
                    {"anomaly_id": anomaly_id, "kpi": anomaly["kpi"], "score": score},
                )
        else:
            self.store.update_anomaly(
                anomaly_id,
                {**anomaly, "status": status, "domain": domain},
                incident_id=incident_id,
            )
            self.store.add_event(
                incident_id,
                "anomaly_added",
                {"anomaly_id": anomaly_id, "kpi": anomaly["kpi"], "reopened": True},
            )
        self._recompute(incident_id, changed=not created)
        return incident_id

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
        import json

        alarms = self.store.list_alarms(incident_id)
        anomalies = self.store.list_anomalies(incident_id)
        firing = [row for row in alarms if row["status"] == "firing"]
        open_anomalies = [row for row in anomalies if row["status"] in {"active", "recovering"}]

        domains: list[str] = []
        for row in alarms:
            labels = json.loads(row["labels_json"])
            domains.append(str(labels.get("domain") or ALERT_DOMAIN.get(row["alertname"], "RAN")))
        for row in anomalies:
            domains.append(str(row["domain"] or KPI_DOMAIN.get(row["kpi"], "RAN")))
        domain = _domain_of(domains)

        if not firing and not open_anomalies:
            if incident["lifecycle_state"] != "cleared":
                self.store.touch(
                    incident_id,
                    lifecycle_state="cleared",
                    cleared_at=utc_now(),
                    probable_domain=domain,
                )
                self.store.add_event(
                    incident_id,
                    "cleared",
                    {"reason": "all contributing alarms and anomalies resolved"},
                )
            return

        severities = [row["severity"] for row in firing]
        for row in open_anomalies:
            severities.append(_anomaly_severity(float(row["score"])))
        severity = _max_severity(severities) if severities else incident["severity"]
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
