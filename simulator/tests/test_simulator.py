"""Unit tests for KPI generation and operating states."""

from __future__ import annotations

from app.config import KPI_RANGES, SECTORS, SITES
from app.failures import IncidentStore
from app.models import FailureType, OperatingState
from app.simulator import NetworkSimulator


def test_topology_has_three_sites_and_nine_sectors():
    sim = NetworkSimulator(seed=1)
    assert len(sim.sectors) == 9
    topo = sim.topology()
    assert len(topo["sites"]) == 3
    for site in topo["sites"]:
        assert site["id"] in SITES
        assert len(site["sectors"]) == 3
        names = {s["name"] for s in site["sectors"]}
        assert names == {"Sector Alpha", "Sector Beta", "Sector Gamma"}
        ids = {s["id"] for s in site["sectors"]}
        assert ids == set(SECTORS)


def test_normal_kpis_within_ranges():
    sim = NetworkSimulator(seed=7)
    for sector in sim.sectors.values():
        assert sector.state == OperatingState.NORMAL
        for kpi_name, ranges in KPI_RANGES.items():
            lo, hi = ranges["normal"]
            value = sector.kpis[kpi_name]
            assert lo <= value <= hi, f"{kpi_name}={value} not in [{lo}, {hi}]"


def test_cell_outage_forces_critical_and_near_zero_availability():
    sim = NetworkSimulator(seed=3)
    store = IncidentStore()
    store.create(FailureType.CELL_OUTAGE, "plte-site-101", "sector-alpha")
    sim.sync_from_incidents(store)

    sector = sim.get_sector("plte-site-101", "sector-alpha")
    assert sector is not None
    assert sector.state == OperatingState.CRITICAL
    assert sector.kpis["availability_pct"] <= 2.0
    assert sector.kpis["dl_throughput_mbps"] <= 0.5

    # Unaffected sector stays normal
    other = sim.get_sector("plte-site-101", "sector-beta")
    assert other is not None
    assert other.state == OperatingState.NORMAL


def test_site_wide_failure_applies_to_all_sectors():
    sim = NetworkSimulator(seed=5)
    store = IncidentStore()
    store.create(FailureType.RF_INTERFERENCE, "plte-site-102", None)
    sim.sync_from_incidents(store)

    for sector_id in SECTORS:
        sector = sim.get_sector("plte-site-102", sector_id)
        assert sector is not None
        assert sector.state == OperatingState.DEGRADED
        assert FailureType.RF_INTERFERENCE in sector.active_failures


def test_worst_state_wins_when_multiple_failures():
    sim = NetworkSimulator(seed=9)
    store = IncidentStore()
    store.create(FailureType.RF_INTERFERENCE, "plte-site-103", "sector-gamma")
    store.create(FailureType.CELL_OUTAGE, "plte-site-103", "sector-gamma")
    sim.sync_from_incidents(store)

    sector = sim.get_sector("plte-site-103", "sector-gamma")
    assert sector is not None
    assert sector.state == OperatingState.CRITICAL


def test_list_sectors_model_roundtrip():
    sim = NetworkSimulator(seed=11)
    models = sim.list_sectors()
    assert len(models) == 9
    assert all(m.state == OperatingState.NORMAL for m in models)
    assert all(m.sector_name.startswith("Sector ") for m in models)
