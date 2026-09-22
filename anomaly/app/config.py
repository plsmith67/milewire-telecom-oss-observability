"""Configuration loader for the anomaly detector."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class KpiConfig:
    name: str
    direction: str
    domain: str
    score_threshold: float
    mad_epsilon: float


@dataclass
class Settings:
    poll_interval_sec: int = 15
    baseline_window: str = "30m"
    baseline_guard_sec: int = 60
    min_samples: int = 20
    score_threshold: float = 3.5
    persist_count: int = 3
    recover_count: int = 2
    stale_sec: int = 90
    ewma_alpha: float = 0.3
    detector_version: str = "hybrid-mad-ewma-v2"
    prometheus_url: str = "http://prometheus:9090"
    incident_webhook_url: str = "http://incident:8080/webhooks/anomalies"
    db_path: str = "/data/anomalies.db"
    sites: list[str] = field(default_factory=lambda: ["plte-site-101", "plte-site-102", "plte-site-103"])
    sectors: list[str] = field(default_factory=lambda: ["sector-alpha", "sector-beta", "sector-gamma"])
    kpis: dict[str, KpiConfig] = field(default_factory=dict)

    def threshold_for(self, kpi: str) -> float:
        item = self.kpis.get(kpi)
        return item.score_threshold if item else self.score_threshold


def load_settings(path: str | None = None) -> Settings:
    config_path = Path(path or os.environ.get("ANOMALY_CONFIG", "config/default.yaml"))
    raw: dict[str, Any] = {}
    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text()) or {}

    kpis: dict[str, KpiConfig] = {}
    for name, meta in (raw.get("kpis") or {}).items():
        kpis[name] = KpiConfig(
            name=name,
            direction=str(meta.get("direction", "lower")),
            domain=str(meta.get("domain", "RAN")),
            score_threshold=float(meta.get("score_threshold", raw.get("score_threshold", 3.5))),
            mad_epsilon=float(meta.get("mad_epsilon", 0.01)),
        )

    settings = Settings(
        poll_interval_sec=int(os.environ.get("ANOMALY_POLL_INTERVAL_SEC", raw.get("poll_interval_sec", 15))),
        baseline_window=str(os.environ.get("ANOMALY_BASELINE_WINDOW", raw.get("baseline_window", "30m"))),
        baseline_guard_sec=int(
            os.environ.get("ANOMALY_BASELINE_GUARD_SEC", raw.get("baseline_guard_sec", 60))
        ),
        min_samples=int(os.environ.get("ANOMALY_MIN_SAMPLES", raw.get("min_samples", 20))),
        score_threshold=float(os.environ.get("ANOMALY_SCORE_THRESHOLD", raw.get("score_threshold", 3.5))),
        persist_count=int(os.environ.get("ANOMALY_PERSIST_COUNT", raw.get("persist_count", 3))),
        recover_count=int(os.environ.get("ANOMALY_RECOVER_COUNT", raw.get("recover_count", 2))),
        stale_sec=int(os.environ.get("ANOMALY_STALE_SEC", raw.get("stale_sec", 90))),
        ewma_alpha=float(os.environ.get("ANOMALY_EWMA_ALPHA", raw.get("ewma_alpha", 0.3))),
        detector_version=str(raw.get("detector_version", "hybrid-mad-ewma-v2")),
        prometheus_url=os.environ.get("PROMETHEUS_URL", "http://prometheus:9090"),
        incident_webhook_url=os.environ.get(
            "INCIDENT_WEBHOOK_URL", "http://incident:8080/webhooks/anomalies"
        ),
        db_path=os.environ.get("ANOMALY_DB_PATH", "/data/anomalies.db"),
        sites=list(raw.get("sites") or ["plte-site-101", "plte-site-102", "plte-site-103"]),
        sectors=list(raw.get("sectors") or ["sector-alpha", "sector-beta", "sector-gamma"]),
        kpis=kpis,
    )
    return settings
