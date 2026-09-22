"""Prometheus metrics for the anomaly service."""

from __future__ import annotations

import json

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, generate_latest

from app.store import AnomalyStore


class MetricsRegistry:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.eval_total = Counter(
            "oss_anomaly_evaluations_total",
            "Anomaly evaluation cycles",
            ["result"],
            registry=self.registry,
        )
        self.anomaly_info = Gauge(
            "oss_anomaly_info",
            "Anomaly record visible to operations views",
            ["anomaly_id", "site", "sector", "kpi", "lifecycle_state", "direction"],
            registry=self.registry,
        )
        self.anomaly_score = Gauge(
            "oss_anomaly_score",
            "Combined anomaly score",
            ["site", "sector", "kpi"],
            registry=self.registry,
        )
        self.anomaly_observed = Gauge(
            "oss_anomaly_observed",
            "Latest observed KPI value for an anomaly series",
            ["site", "sector", "kpi"],
            registry=self.registry,
        )
        self.anomaly_baseline = Gauge(
            "oss_anomaly_baseline",
            "Baseline median for an anomaly series",
            ["site", "sector", "kpi"],
            registry=self.registry,
        )
        self.open_anomalies = Gauge(
            "oss_open_anomalies",
            "Count of active or recovering anomalies",
            ["lifecycle_state"],
            registry=self.registry,
        )

    def refresh(self, store: AnomalyStore) -> None:
        self.anomaly_info.clear()
        self.anomaly_score.clear()
        self.anomaly_observed.clear()
        self.anomaly_baseline.clear()
        self.open_anomalies.clear()
        counts: dict[str, int] = {}
        for row in store.recent_for_metrics():
            self.anomaly_info.labels(
                anomaly_id=row["id"],
                site=row["site"],
                sector=row["sector"],
                kpi=row["kpi"],
                lifecycle_state=row["lifecycle_state"],
                direction=row["direction"],
            ).set(1)
            self.anomaly_score.labels(site=row["site"], sector=row["sector"], kpi=row["kpi"]).set(
                row["combined_score"]
            )
            self.anomaly_observed.labels(site=row["site"], sector=row["sector"], kpi=row["kpi"]).set(
                row["observed_value"]
            )
            self.anomaly_baseline.labels(site=row["site"], sector=row["sector"], kpi=row["kpi"]).set(
                row["baseline_median"]
            )
            if row["lifecycle_state"] in {"active", "recovering"}:
                counts[row["lifecycle_state"]] = counts.get(row["lifecycle_state"], 0) + 1
        for state, count in counts.items():
            self.open_anomalies.labels(lifecycle_state=state).set(count)

    def expose(self) -> tuple[bytes, str]:
        return generate_latest(self.registry), CONTENT_TYPE_LATEST


def anomaly_to_dict(row, *, include_window: bool = False) -> dict:
    payload = {
        "id": row["id"],
        "fingerprint": row["fingerprint"],
        "site": row["site"],
        "sector": row["sector"],
        "kpi": row["kpi"],
        "observed_value": row["observed_value"],
        "baseline_median": row["baseline_median"],
        "baseline_dispersion": row["baseline_dispersion"],
        "robust_z": row["robust_z"],
        "ewma_score": row["ewma_score"],
        "combined_score": row["combined_score"],
        "direction": row["direction"],
        "lifecycle_state": row["lifecycle_state"],
        "first_detected": row["first_detected"],
        "last_observed": row["last_observed"],
        "cleared_at": row["cleared_at"],
        "detector_version": row["detector_version"],
        "incident_id": row["incident_id"],
    }
    if include_window:
        payload["sample_window"] = json.loads(row["sample_window_json"])
    return payload
