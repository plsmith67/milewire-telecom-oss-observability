"""Missing and stale sample handling."""

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
    def publish(self, anomaly):
        return {}


def test_stale_samples_skipped(tmp_path):
    store = AnomalyStore(str(tmp_path / "s.db"))
    kpi = KpiConfig("lte_latency_ms", "higher", "Transport", 3.5, 0.2)
    settings = Settings(
        min_samples=3,
        stale_sec=30,
        sites=["plte-site-101"],
        sectors=["sector-alpha"],
        kpis={"lte_latency_ms": kpi},
    )
    old = time.time() - 120
    prom = FakeProm([Sample(ts=old + i * 15, value=15.0) for i in range(10)])
    engine = AnomalyEngine(settings, store, prom, FakePublisher(), MetricsRegistry())
    result = engine.run_once()
    assert result["evaluated"] == 1
    assert store.list() == []


def test_insufficient_baseline_no_row(tmp_path):
    store = AnomalyStore(str(tmp_path / "m.db"))
    kpi = KpiConfig("lte_latency_ms", "higher", "Transport", 3.5, 0.2)
    settings = Settings(
        min_samples=20,
        sites=["plte-site-101"],
        sectors=["sector-alpha"],
        kpis={"lte_latency_ms": kpi},
    )
    now = time.time()
    prom = FakeProm([Sample(ts=now - 15 * i, value=15.0) for i in range(5)][::-1])
    engine = AnomalyEngine(settings, store, prom, FakePublisher(), MetricsRegistry())
    engine.run_once()
    assert store.list() == []
