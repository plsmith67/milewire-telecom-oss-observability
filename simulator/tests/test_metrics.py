"""Tests for Prometheus /metrics exposition."""

from __future__ import annotations

EXPECTED_METRICS = [
    "lte_rsrp_dbm",
    "lte_rsrq_db",
    "lte_sinr_db",
    "lte_dl_throughput_mbps",
    "lte_ul_throughput_mbps",
    "lte_packet_loss_pct",
    "lte_latency_ms",
    "lte_availability_pct",
    "lte_active_users",
    "lte_handover_success_pct",
    "lte_active_incidents",
    "lte_incident_info",
    "lte_sector_state",
]


def test_metrics_endpoint_exposes_all_series(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text
    for name in EXPECTED_METRICS:
        assert name in body, f"missing metric {name}"


def test_metrics_include_site_and_sector_labels(client):
    body = client.get("/metrics").text
    assert 'site="plte-site-101"' in body
    assert 'sector="sector-alpha"' in body
    assert 'sector="sector-gamma"' in body
    assert 'state="normal"' in body
    assert "cell-1" not in body
    assert "cell-3" not in body


def test_metrics_reflect_active_incident(client):
    client.post(
        "/failures/cell-outage",
        json={"site": "plte-site-103", "sector": "sector-gamma"},
    )
    body = client.get("/metrics").text
    assert "lte_active_incidents" in body
    assert 'failure_type="cell_outage"' in body
    assert 'sector="sector-gamma"' in body
    assert 'state="critical"' in body
