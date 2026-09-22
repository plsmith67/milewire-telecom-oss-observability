"""Overlapping failure composition must be monotonic (worst wins)."""

from __future__ import annotations

from app.config import KPI_RANGES, SOFT_DRIFT_RANGES
from app.models import FailureType
from app.simulator import HIGHER_IS_BETTER, NetworkSimulator, compose_kpi


SITE, SECTOR = "plte-site-103", "sector-gamma"
SINR_WARN = 10.0
RF_SINR_MAX = KPI_RANGES["sinr_db"]["critical"][1]  # 3.0


def _sector(client, site=SITE, sector=SECTOR):
    sectors = {(c["site"], c["sector"]): c for c in client.get("/api/v1/sectors").json()}
    return sectors[(site, sector)]


def _post(client, path, site=SITE, sector=SECTOR):
    resp = client.post(path, json={"site": site, "sector": sector})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_compose_kpi_monotonic_helpers():
    assert compose_kpi("sinr_db", 12.0, 1.0) == 1.0
    assert compose_kpi("sinr_db", 1.0, 12.0) == 1.0
    assert compose_kpi("latency_ms", 30.0, 200.0) == 200.0
    assert compose_kpi("latency_ms", 200.0, 30.0) == 200.0
    assert "sinr_db" in HIGHER_IS_BETTER


def test_soft_drift_alone_keeps_sinr_in_band(client):
    _post(client, "/failures/soft-kpi-drift")
    target = _sector(client)
    assert target["state"] == "normal"
    lo, hi = SOFT_DRIFT_RANGES["sinr_db"]
    assert lo <= target["sinr_db"] <= hi
    assert target["sinr_db"] >= SINR_WARN


def test_rf_interference_alone_crosses_sinr_threshold(client):
    _post(client, "/failures/rf-interference")
    target = _sector(client)
    assert target["state"] == "degraded"
    assert target["sinr_db"] <= RF_SINR_MAX
    assert target["sinr_db"] < SINR_WARN


def test_soft_drift_plus_rf_crosses_sinr_threshold(client):
    _post(client, "/failures/soft-kpi-drift")
    soft = _sector(client)
    assert 11.8 <= soft["sinr_db"] <= 12.2

    _post(client, "/failures/rf-interference")
    both = _sector(client)
    assert both["state"] == "degraded"
    assert set(both["active_failures"]) >= {"soft_kpi_drift", "rf_interference"}
    assert both["sinr_db"] < SINR_WARN
    assert both["sinr_db"] <= RF_SINR_MAX


def test_adding_rf_cannot_improve_affected_kpis(client):
    _post(client, "/failures/soft-kpi-drift")
    before = _sector(client)
    _post(client, "/failures/rf-interference")
    after = _sector(client)

    # RF-affected higher-is-better KPIs must not improve.
    assert after["sinr_db"] <= before["sinr_db"]
    assert after["rsrp_dbm"] <= before["rsrp_dbm"]
    assert after["rsrq_db"] <= before["rsrq_db"]


def test_clear_rf_while_soft_remains_returns_soft_band(client):
    soft = _post(client, "/failures/soft-kpi-drift")
    rf = _post(client, "/failures/rf-interference")
    assert _sector(client)["sinr_db"] < SINR_WARN

    deleted = client.delete(f"/failures/{rf['id']}")
    assert deleted.status_code == 200
    remaining = client.get("/failures").json()
    assert len(remaining) == 1
    assert remaining[0]["id"] == soft["id"]

    target = _sector(client)
    lo, hi = SOFT_DRIFT_RANGES["sinr_db"]
    assert lo <= target["sinr_db"] <= hi
    assert target["sinr_db"] >= SINR_WARN
    assert target["state"] == "normal"


def test_clear_both_returns_normal(client):
    _post(client, "/failures/soft-kpi-drift")
    _post(client, "/failures/rf-interference")
    cleared = client.delete("/failures")
    assert cleared.json()["cleared"] == 2
    target = _sector(client)
    assert target["active_failures"] == []
    assert target["state"] == "normal"
    # Normal SINR band from KPI_RANGES
    lo, hi = KPI_RANGES["sinr_db"]["normal"]
    assert lo <= target["sinr_db"] <= hi


def test_cell_outage_dominates_soft_and_rf(client):
    _post(client, "/failures/soft-kpi-drift")
    _post(client, "/failures/rf-interference")
    _post(client, "/failures/cell-outage")
    target = _sector(client)
    assert target["state"] == "critical"
    assert target["availability_pct"] <= 2.0
    assert target["sinr_db"] <= RF_SINR_MAX
    assert target["active_users"] <= 3.0


def test_results_independent_of_failure_insertion_order(client):
    # Order A: soft then RF
    _post(client, "/failures/soft-kpi-drift")
    _post(client, "/failures/rf-interference")
    a = _sector(client)
    client.delete("/failures")

    # Order B: RF then soft
    _post(client, "/failures/rf-interference")
    _post(client, "/failures/soft-kpi-drift")
    b = _sector(client)

    assert a["state"] == b["state"] == "degraded"
    assert a["sinr_db"] < SINR_WARN and b["sinr_db"] < SINR_WARN
    assert a["sinr_db"] <= RF_SINR_MAX and b["sinr_db"] <= RF_SINR_MAX
    # Both in critical RF band (order must not restore soft-drift band)
    assert not (11.8 <= a["sinr_db"] <= 12.2)
    assert not (11.8 <= b["sinr_db"] <= 12.2)


def test_derive_state_uses_highest_severity():
    assert (
        NetworkSimulator._derive_state([FailureType.SOFT_KPI_DRIFT])
        .value
        == "normal"
    )
    assert (
        NetworkSimulator._derive_state(
            [FailureType.SOFT_KPI_DRIFT, FailureType.RF_INTERFERENCE]
        ).value
        == "degraded"
    )
    assert (
        NetworkSimulator._derive_state(
            [
                FailureType.SOFT_KPI_DRIFT,
                FailureType.RF_INTERFERENCE,
                FailureType.CELL_OUTAGE,
            ]
        ).value
        == "critical"
    )
