"""Persistence and transient suppression."""

import time

from app.config import KpiConfig, Settings
from app.engine import AnomalyEngine
from app.metrics import MetricsRegistry
from app.prom_client import Sample
from app.store import AnomalyStore


class FakeProm:
    def __init__(self, series: list[Sample]):
        self.series = series

    def query_range_values(self, metric, site, sector, window, step="15s", lookback_extra_sec=0):
        return list(self.series)

    def healthy(self):
        return True


class FakePublisher:
    def __init__(self):
        self.payloads = []

    def publish(self, anomaly):
        self.payloads.append(anomaly)
        return {"ok": True}


def _settings(**overrides):
    kpi = KpiConfig(
        name="lte_sinr_db",
        direction="lower",
        domain="RF",
        score_threshold=3.0,
        mad_epsilon=0.05,
    )
    base = Settings(
        min_samples=5,
        persist_count=3,
        recover_count=2,
        stale_sec=3600,
        baseline_guard_sec=60,
        sites=["plte-site-103"],
        sectors=["sector-gamma"],
        kpis={"lte_sinr_db": kpi},
        detector_version="hybrid-mad-ewma-v2",
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def _series(values: list[float]) -> list[Sample]:
    start = time.time() - 15 * (len(values) - 1)
    return [Sample(ts=start + i * 15, value=v) for i, v in enumerate(values)]


def test_transient_suppressed_until_persist(tmp_path):
    store = AnomalyStore(str(tmp_path / "a.db"))
    baseline = [20.0] * 10
    prom = FakeProm(_series(baseline + [8.0]))
    publisher = FakePublisher()
    engine = AnomalyEngine(_settings(), store, prom, publisher, MetricsRegistry())

    for _ in range(2):
        prom.series = _series(baseline + [8.0])
        result = engine.run_once()
        assert result["published"] == 0

    rows = store.list()
    assert len(rows) == 1
    assert rows[0]["lifecycle_state"] == "warming_up"

    prom.series = _series(baseline + [8.0])
    result = engine.run_once()
    assert result["published"] == 1
    assert store.list()[0]["lifecycle_state"] == "active"
    assert len(publisher.payloads) == 1


def test_recovery_clears(tmp_path):
    store = AnomalyStore(str(tmp_path / "b.db"))
    publisher = FakePublisher()
    engine = AnomalyEngine(
        _settings(persist_count=1),
        store,
        FakeProm(_series([20.0] * 10 + [5.0])),
        publisher,
        MetricsRegistry(),
    )
    engine.run_once()
    assert store.list()[0]["lifecycle_state"] == "active"

    engine.prom = FakeProm(_series([20.0] * 12))
    engine.run_once()
    engine.run_once()
    row = store.get(store.list()[0]["id"])
    assert row["lifecycle_state"] == "cleared"
    assert row["cleared_at"]
