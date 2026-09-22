"""Prometheus metrics derived from the incident store."""

from __future__ import annotations

import json
import os

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, generate_latest

from app import ai as ai_mod
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
        self.anomaly_webhook_requests = Counter(
            "oss_anomaly_webhook_requests_total",
            "Anomaly webhook requests received",
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
        self.incident_anomaly_count = Gauge(
            "oss_incident_anomaly_count",
            "Open anomaly contributions on an incident",
            ["incident_id", "site", "sector"],
            registry=self.registry,
        )
        self.ai_status = Gauge(
            "oss_ai_analysis_status",
            "Latest AI analysis status gauge (1 for current status)",
            ["status", "incident_id"],
            registry=self.registry,
        )
        self.ai_cause_info = Gauge(
            "oss_ai_cause_info",
            "Probable causes from latest completed AI analysis",
            ["incident_id", "rank", "cause"],
            registry=self.registry,
        )
        self.ai_check_info = Gauge(
            "oss_ai_check_info",
            "Recommended operator checks from latest completed AI analysis",
            ["incident_id", "rank", "check"],
            registry=self.registry,
        )
        self.ai_evidence_ref_info = Gauge(
            "oss_ai_evidence_ref_info",
            "Evidence references from latest completed AI analysis",
            ["incident_id", "rank", "ref"],
            registry=self.registry,
        )

    def refresh(self, store: IncidentStore) -> None:
        self.incident_info.clear()
        self.open_incidents.clear()
        self.incident_anomaly_count.clear()
        self.ai_status.clear()
        self.ai_cause_info.clear()
        self.ai_check_info.clear()
        self.ai_evidence_ref_info.clear()
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
            open_anom = store.count_open_anomalies(row["id"])
            if open_anom or store.list_anomalies(row["id"]):
                self.incident_anomaly_count.labels(
                    incident_id=row["id"], site=row["site"], sector=row["sector"]
                ).set(open_anom)
            if row["lifecycle_state"] in {"active", "acknowledged"}:
                key = (row["severity"], row["lifecycle_state"])
                counts[key] = counts.get(key, 0) + 1
        for (severity, state), count in counts.items():
            self.open_incidents.labels(severity=severity, lifecycle_state=state).set(count)

        if not ai_mod.provider_configured():
            self.ai_status.labels(status="unavailable", incident_id="none").set(1)
        latest = store.latest_analysis()
        if latest is not None:
            self.ai_status.labels(status=latest["status"], incident_id=latest["incident_id"]).set(1)
            if latest["status"] == "completed" and latest["response_json"]:
                payload = json.loads(latest["response_json"])
                for idx, cause in enumerate(payload.get("probable_causes") or [], start=1):
                    self.ai_cause_info.labels(
                        incident_id=latest["incident_id"],
                        rank=str(idx),
                        cause=str(cause)[:120],
                    ).set(1)
                for idx, check in enumerate(payload.get("recommended_operator_checks") or [], start=1):
                    self.ai_check_info.labels(
                        incident_id=latest["incident_id"],
                        rank=str(idx),
                        check=str(check)[:120],
                    ).set(1)
                for idx, ref in enumerate(payload.get("evidence_references") or [], start=1):
                    self.ai_evidence_ref_info.labels(
                        incident_id=latest["incident_id"],
                        rank=str(idx),
                        ref=str(ref)[:120],
                    ).set(1)

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
        "evidence_type": "threshold_alarm",
    }


def anomaly_contrib_to_dict(row) -> dict:
    evidence = json.loads(row["evidence_json"])
    return {
        "id": row["id"],
        "incident_id": row["incident_id"],
        "anomaly_id": row["anomaly_id"],
        "kpi": row["kpi"],
        "score": row["score"],
        "status": row["status"],
        "domain": row["domain"],
        "first_seen_at": row["first_seen_at"],
        "last_seen_at": row["last_seen_at"],
        "evidence": evidence,
        "evidence_type": "statistical_anomaly",
    }


def incident_to_dict(store: IncidentStore, row, *, include_alarms: bool = False) -> dict:
    alarms = store.list_alarms(row["id"])
    anomalies = store.list_anomalies(row["id"])
    kpis = sorted(
        {
            *(item["affected_kpi"] for item in alarms if item["affected_kpi"]),
            *(item["kpi"] for item in anomalies if item["kpi"]),
        }
    )
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
        "contributing_anomaly_count": len(anomalies),
        "open_anomaly_count": sum(
            1 for item in anomalies if item["status"] in {"active", "recovering"}
        ),
        "affected_kpis": kpis,
    }
    if include_alarms:
        payload["alarms"] = [alarm_to_dict(item) for item in alarms]
        payload["anomalies"] = [anomaly_contrib_to_dict(item) for item in anomalies]
    return payload
