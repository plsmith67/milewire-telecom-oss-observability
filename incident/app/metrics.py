"""Prometheus metrics derived from the incident store."""

from __future__ import annotations

import json

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, generate_latest

from app.store import IncidentStore


class MetricsRegistry:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.webhook_requests = Counter(
            "oss_webhook_requests_total",
            "Alertmanager webhook requests received",
            ["result"],
            registry=self.registry,
        )
        self.incident_info = Gauge(
            "oss_incident_info",
            "Incident record (1 while visible to operations views)",
            ["incident_id", "site", "sector", "severity", "lifecycle_state", "probable_domain"],
            registry=self.registry,
        )
        self.open_incidents = Gauge(
            "oss_open_incidents",
            "Count of incidents that are active or acknowledged",
            ["severity", "lifecycle_state"],
            registry=self.registry,
        )

    def refresh(self, store: IncidentStore) -> None:
        self.incident_info.clear()
        self.open_incidents.clear()
        counts: dict[tuple[str, str], int] = {}
        for row in store.recent_for_metrics():
            self.incident_info.labels(
                incident_id=row["id"],
                site=row["site"],
                sector=row["sector"],
                severity=row["severity"],
                lifecycle_state=row["lifecycle_state"],
                probable_domain=row["probable_domain"],
            ).set(1)
            if row["lifecycle_state"] in {"active", "acknowledged"}:
                key = (row["severity"], row["lifecycle_state"])
                counts[key] = counts.get(key, 0) + 1
        for (severity, state), count in counts.items():
            self.open_incidents.labels(severity=severity, lifecycle_state=state).set(count)

    def expose(self) -> tuple[bytes, str]:
        return generate_latest(self.registry), CONTENT_TYPE_LATEST


def alarm_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "incident_id": row["incident_id"],
        "fingerprint": row["fingerprint"],
        "alertname": row["alertname"],
        "severity": row["severity"],
        "status": row["status"],
        "affected_kpi": row["affected_kpi"],
        "starts_at": row["starts_at"],
        "ends_at": row["ends_at"],
        "last_seen_at": row["last_seen_at"],
        "labels": json.loads(row["labels_json"]),
        "annotations": json.loads(row["annotations_json"]),
    }


def incident_to_dict(store: IncidentStore, row, *, include_alarms: bool = False) -> dict:
    alarms = store.list_alarms(row["id"])
    kpis = sorted({item["affected_kpi"] for item in alarms if item["affected_kpi"]})
    payload = {
        "id": row["id"],
        "site": row["site"],
        "sector": row["sector"],
        "severity": row["severity"],
        "lifecycle_state": row["lifecycle_state"],
        "probable_domain": row["probable_domain"],
        "first_detected": row["first_detected"],
        "last_updated": row["last_updated"],
        "cleared_at": row["cleared_at"],
        "acknowledged_at": row["acknowledged_at"],
        "acknowledged_by": row["acknowledged_by"],
        "contributing_alarm_count": len(alarms),
        "firing_alarm_count": sum(1 for item in alarms if item["status"] == "firing"),
        "affected_kpis": kpis,
    }
    if include_alarms:
        payload["alarms"] = [alarm_to_dict(item) for item in alarms]
    return payload
