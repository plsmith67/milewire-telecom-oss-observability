"""Soft KPI drift stays inside Phase 2 warning thresholds."""

from app.config import SOFT_DRIFT_RANGES


def test_soft_kpi_drift_sub_warning(client):
    resp = client.post(
        "/failures/soft-kpi-drift",
        json={"site": "plte-site-103", "sector": "sector-gamma"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["failure_type"] == "soft_kpi_drift"
    assert body["operating_state"] == "normal"

    sectors = {
        (item["site"], item["sector"]): item for item in client.get("/api/v1/sectors").json()
    }
    target = sectors[("plte-site-103", "sector-gamma")]
    assert target["state"] == "normal"
    assert "soft_kpi_drift" in target["active_failures"]

    assert SOFT_DRIFT_RANGES["rsrp_dbm"][0] <= target["rsrp_dbm"] <= SOFT_DRIFT_RANGES["rsrp_dbm"][1]
    assert target["rsrp_dbm"] > -95  # Phase 2 warning threshold
    assert SOFT_DRIFT_RANGES["sinr_db"][0] <= target["sinr_db"] <= SOFT_DRIFT_RANGES["sinr_db"][1]
    assert 11.8 <= target["sinr_db"] <= 12.2  # narrow sub-warning demo band
    assert target["sinr_db"] >= 10  # Phase 2 PLTELowSINR warning is < 10
    assert SOFT_DRIFT_RANGES["latency_ms"][0] <= target["latency_ms"] <= SOFT_DRIFT_RANGES["latency_ms"][1]
    assert target["latency_ms"] <= 40  # Phase 2 warning is > 40
