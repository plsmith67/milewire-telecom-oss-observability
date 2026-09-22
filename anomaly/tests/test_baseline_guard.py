"""Baseline window separation and freeze behavior."""

import time

from app.config import KpiConfig, Settings
from app.detector import combine_scores, evaluate
from app.engine import AnomalyEngine, split_baseline_observation
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


def _series(values: list[float], step: float = 15.0) -> list[Sample]:
    start = time.time() - step * (len(values) - 1)
    return [Sample(ts=start + i * step, value=v) for i, v in enumerate(values)]


def test_observation_excluded_from_reference_baseline():
    # Guard of 60s with 15s steps excludes the last three samples (45s) from baseline.
    values = [19.1] * 20 + [12.0, 12.0, 12.0]
    samples = _series(values)
    split = split_baseline_observation(samples, guard_sec=60)
    assert split is not None
    baseline, obs = split
    assert obs.value == 12.0
    assert all(abs(s.value - 19.1) < 1e-9 for s in baseline)
    assert len(baseline) >= 5
    # Contaminated points remain in the full series but not in the reference.
    assert any(abs(s.value - 12.0) < 1e-9 for s in samples[:-1])
    assert all(s.ts <= obs.ts - 60 for s in baseline)


def test_combine_scores_uses_max_not_average():
    assert combine_scores(3.0, 0.5) == 3.0
    assert combine_scores(1.0, 4.0) == 4.0
    result = evaluate(
        [19.1] * 25,
        observed=12.0,
        direction="lower",
        epsilon=0.05,
        threshold=3.0,
        ewma_prev=19.1,
        alpha=0.3,
    )
    # Clean MAD~0 on constant series floor -> epsilon path; still strong z.
    assert result.combined_score == max(result.robust_z, result.ewma_score)
    assert result.is_candidate


def test_clean_baseline_score_near_12_db():
    # Approximate acceptance clean baseline: median 19.1, MAD 1.58.
    baseline = [19.1 - 1.58, 19.1, 19.1 + 1.58] * 10
    result = evaluate(
        baseline,
        observed=12.0,
        direction="lower",
        epsilon=0.05,
        threshold=3.0,
        ewma_prev=19.1,
        alpha=0.3,
    )
    assert result.robust_z >= 3.0
    assert result.combined_score >= 3.0
    assert result.is_candidate


def test_baseline_frozen_during_active_degradation(tmp_path):
    store = AnomalyStore(str(tmp_path / "freeze.db"))
    publisher = FakePublisher()
    clean = [19.1] * 20
    # First three observations at 12.0 open and activate with frozen baseline.
    engine = AnomalyEngine(
        _settings(persist_count=3, min_samples=10),
        store,
        FakeProm(_series(clean + [12.0])),
        publisher,
        MetricsRegistry(),
    )
    for _ in range(3):
        engine.prom = FakeProm(_series(clean + [12.0]))
        engine.run_once()
    row = store.list(state="active")[0]
    frozen_median = row["baseline_median"]
    frozen_sigma = row["baseline_dispersion"]

    # Contaminate the query window heavily; frozen stats must not move.
    contaminated = [12.0] * 25
    engine.prom = FakeProm(_series(contaminated))
    engine.run_once()
    updated = store.get(row["id"])
    assert updated["lifecycle_state"] == "active"
    assert updated["baseline_median"] == frozen_median
    assert updated["baseline_dispersion"] == frozen_sigma


def test_normal_traffic_does_not_trigger(tmp_path):
    store = AnomalyStore(str(tmp_path / "normal.db"))
    engine = AnomalyEngine(
        _settings(persist_count=3, min_samples=10),
        store,
        FakeProm(_series([19.0 + (i % 3) * 0.2 for i in range(30)])),
        FakePublisher(),
        MetricsRegistry(),
    )
    for _ in range(5):
        engine.run_once()
    assert store.list(state="active") == []
    assert store.list(state="warming_up") == []


def test_three_persistent_subwarning_sinr_trigger(tmp_path):
    store = AnomalyStore(str(tmp_path / "persist.db"))
    publisher = FakePublisher()
    clean = [19.1] * 20
    engine = AnomalyEngine(
        _settings(persist_count=3, min_samples=10),
        store,
        FakeProm(_series(clean + [12.0])),
        publisher,
        MetricsRegistry(),
    )
    for _ in range(2):
        engine.prom = FakeProm(_series(clean + [12.0]))
        result = engine.run_once()
        assert result["published"] == 0
    assert store.list()[0]["lifecycle_state"] == "warming_up"

    engine.prom = FakeProm(_series(clean + [12.0]))
    result = engine.run_once()
    assert result["published"] == 1
    row = store.list(state="active")[0]
    assert row["kpi"] == "lte_sinr_db"
    assert row["observed_value"] >= 10.0  # still above Phase 2 warning
    assert row["combined_score"] >= 3.0


def test_one_or_two_transients_do_not_activate(tmp_path):
    store = AnomalyStore(str(tmp_path / "transient.db"))
    clean = [19.1] * 20
    engine = AnomalyEngine(
        _settings(persist_count=3, min_samples=10),
        store,
        FakeProm(_series(clean + [12.0])),
        FakePublisher(),
        MetricsRegistry(),
    )
    engine.run_once()
    assert store.list()[0]["lifecycle_state"] == "warming_up"
    engine.prom = FakeProm(_series(clean + [12.0]))
    engine.run_once()
    assert store.list()[0]["lifecycle_state"] == "warming_up"
    assert store.list(state="active") == []


def test_recovery_clears_after_misses(tmp_path):
    store = AnomalyStore(str(tmp_path / "recover.db"))
    publisher = FakePublisher()
    clean = [19.1] * 20
    engine = AnomalyEngine(
        _settings(persist_count=1, recover_count=2, min_samples=10),
        store,
        FakeProm(_series(clean + [12.0])),
        publisher,
        MetricsRegistry(),
    )
    engine.run_once()
    assert store.list()[0]["lifecycle_state"] == "active"

    engine.prom = FakeProm(_series(clean + [19.1]))
    engine.run_once()
    assert store.list()[0]["lifecycle_state"] == "recovering"
    engine.run_once()
    row = store.get(store.list()[0]["id"])
    assert row["lifecycle_state"] == "cleared"
    assert row["cleared_at"]


def test_subwarning_sinr_stays_above_static_alert_band(tmp_path):
    """Early anomaly uses ~12 dB SINR; Phase 2 PLTELowSINR warning is < 10 dB."""
    store = AnomalyStore(str(tmp_path / "alert.db"))
    publisher = FakePublisher()
    clean = [19.1] * 20
    observed = 12.0
    assert observed >= 10.0
    engine = AnomalyEngine(
        _settings(persist_count=3, min_samples=10),
        store,
        FakeProm(_series(clean + [observed])),
        publisher,
        MetricsRegistry(),
    )
    for _ in range(3):
        engine.prom = FakeProm(_series(clean + [observed]))
        engine.run_once()
    row = store.list(state="active")[0]
    assert row["observed_value"] >= 10.0
    # Document static alert inactivity: detector evidence alone, no threshold breach.
    assert row["observed_value"] > 10.0  # PLTELowSINR warning expr requires < 10
