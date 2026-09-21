"""API tests for failure injection scenarios."""

from __future__ import annotations


def test_health_and_ready(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/ready").json()["status"] == "ready"


def test_topology_endpoint(client):
    data = client.get("/api/v1/topology").json()
    assert len(data["sites"]) == 3
    assert data["sites"][0]["id"] == "plte-site-101"
    assert data["sites"][0]["sectors"][0]["id"] == "sector-alpha"
    assert data["sites"][0]["sectors"][0]["name"] == "Sector Alpha"
    assert data["sites"][0]["sectors"][2]["id"] == "sector-gamma"
    assert data["sites"][0]["sectors"][2]["name"] == "Sector Gamma"


def test_rf_interference(client):
    resp = client.post(
        "/failures/rf-interference",
        json={"site": "plte-site-101", "sector": "sector-alpha"},
    )
    assert resp.status_code == 201
    incident = resp.json()
    assert incident["failure_type"] == "rf_interference"
    assert incident["operating_state"] == "degraded"
    assert incident["sector"] == "sector-alpha"

    sectors = {
        (c["site"], c["sector"]): c for c in client.get("/api/v1/sectors").json()
    }
    target = sectors[("plte-site-101", "sector-alpha")]
    assert target["state"] == "degraded"
    assert "rf_interference" in target["active_failures"]
    assert target["sinr_db"] <= 10


def test_backhaul_degradation(client):
    resp = client.post(
        "/failures/backhaul-degradation",
        json={"site": "plte-site-102", "sector": "sector-beta"},
    )
    assert resp.status_code == 201
    sectors = {
        (c["site"], c["sector"]): c for c in client.get("/api/v1/sectors").json()
    }
    target = sectors[("plte-site-102", "sector-beta")]
    assert target["state"] == "degraded"
    assert target["latency_ms"] >= 40
    assert target["packet_loss_pct"] >= 1


def test_cell_outage(client):
    resp = client.post(
        "/failures/cell-outage",
        json={"site": "plte-site-103", "sector": "sector-gamma"},
    )
    assert resp.status_code == 201
    assert resp.json()["operating_state"] == "critical"
    assert resp.json()["sector"] == "sector-gamma"

    sectors = {
        (c["site"], c["sector"]): c for c in client.get("/api/v1/sectors").json()
    }
    target = sectors[("plte-site-103", "sector-gamma")]
    assert target["state"] == "critical"
    assert target["availability_pct"] <= 2.0
    assert target["sector_name"] == "Sector Gamma"


def test_capacity_congestion(client):
    resp = client.post(
        "/failures/capacity-congestion",
        json={"site": "plte-site-101"},
    )
    assert resp.status_code == 201

    sectors = [
        c for c in client.get("/api/v1/sectors").json() if c["site"] == "plte-site-101"
    ]
    assert len(sectors) == 3
    for sector in sectors:
        assert sector["state"] == "degraded"
        assert sector["active_users"] >= 100


def test_list_and_clear_failures(client):
    client.post(
        "/failures/rf-interference",
        json={"site": "plte-site-101", "sector": "sector-alpha"},
    )
    client.post(
        "/failures/cell-outage",
        json={"site": "plte-site-102", "sector": "sector-alpha"},
    )

    listed = client.get("/failures").json()
    assert len(listed) == 2

    failure_id = listed[0]["id"]
    deleted = client.delete(f"/failures/{failure_id}")
    assert deleted.status_code == 200
    assert len(client.get("/failures").json()) == 1

    cleared = client.delete("/failures")
    assert cleared.json()["cleared"] == 1
    assert client.get("/failures").json() == []


def test_delete_unknown_failure_returns_404(client):
    resp = client.delete("/failures/does-not-exist")
    assert resp.status_code == 404


def test_invalid_site_returns_400(client):
    resp = client.post(
        "/failures/rf-interference",
        json={"site": "site-unknown", "sector": "sector-alpha"},
    )
    assert resp.status_code == 400


def test_invalid_sector_returns_400(client):
    resp = client.post(
        "/failures/rf-interference",
        json={"site": "plte-site-101", "sector": "sector-charlie"},
    )
    assert resp.status_code == 400
