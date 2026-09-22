"""Lifecycle and duplicate publish behavior."""

import time

from app.config import KpiConfig, Settings
from app.engine import AnomalyEngine
from app.metrics import MetricsRegistry
from app.prom_client import Sample
from app.store import AnomalyStore


class FakeProm:
    def __init__(self, series):
        self.series = series

    def query_range_values(self, *args, **kwargs):
        return list(self.series)


class FakePublisher:
    def __init__(self):
        self.payloads = []

    def publish(self, anomaly):
        self.payloads.append(anomaly)
        return {}


def test_duplicate_active_updates_same_fingerprint(tmp_path):
    store = AnomalyStore(str(tmp_path / "d.db"))
    kpi = KpiConfig("lte_rsrp_dbm", "lower", "RF", 3.0, 0.05)
    settings = Settings(
        min_samples=5,
        persist_count=1,
        stale_sec=3600,
        baseline_guard_sec=60,
        sites=["plte-site-103"],
        sectors=["sector-gamma"],
        kpis={"lte_rsrp_dbm": kpi},
    )
    publisher = FakePublisher()
    values = [-75.0] * 8 + [-92.0]
    start = time.time() - 15 * (len(values) - 1)
    prom = FakeProm([Sample(ts=start + i * 15, value=v) for i, v in enumerate(values)])
    engine = AnomalyEngine(settings, store, prom, publisher, MetricsRegistry())
    engine.run_once()
    engine.run_once()
    rows = store.list(state="active")
    assert len(rows) == 1
    assert len(publisher.payloads) >= 1
    assert publisher.payloads[0]["kpi"] == "lte_rsrp_dbm"
