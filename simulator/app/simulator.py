"""Sector KPI generation with normal / degraded / critical operating states.

Overlapping failures compose monotonically: each failure proposes candidate KPI
values that are merged with the running value using worst-of semantics so that
adding a failure can never improve an already-degraded KPI.

  higher-is-better KPIs → min(current, proposed)
  lower-is-better  KPIs → max(current, proposed)

Processing order does not change outcomes under this rule. Cell outage proposals
use the worst bands and therefore dominate other failures.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from app.config import (
    KPI_RANGES,
    SECTOR_DISPLAY_NAMES,
    SECTORS,
    SITES,
    SOFT_DRIFT_RANGES,
    STATE_SEVERITY,
)
from app.models import FailureType, OperatingState, SectorKPIs

if TYPE_CHECKING:
    from app.failures import IncidentStore


# Higher numeric value is healthier / better.
HIGHER_IS_BETTER: Final[frozenset[str]] = frozenset(
    {
        "rsrp_dbm",
        "rsrq_db",
        "sinr_db",
        "availability_pct",
        "dl_throughput_mbps",
        "ul_throughput_mbps",
        "handover_success_pct",
    }
)

# Higher numeric value is worse (latency, loss, congestion/utilization).
LOWER_IS_BETTER: Final[frozenset[str]] = frozenset(
    {
        "latency_ms",
        "packet_loss_pct",
        "active_users",
    }
)


def _sample(lo: float, hi: float, rng: random.Random) -> float:
    mid = (lo + hi) / 2.0
    spread = (hi - lo) / 4.0
    value = rng.gauss(mid, max(spread, 0.01))
    return max(lo, min(hi, value))


def compose_kpi(name: str, current: float, proposed: float) -> float:
    """Merge a proposed failure effect into the current KPI (worst wins)."""
    if name in HIGHER_IS_BETTER:
        return min(current, proposed)
    if name in LOWER_IS_BETTER:
        return max(current, proposed)
    raise KeyError(f"Unknown KPI composition direction for {name}")


@dataclass
class SectorRuntime:
    site: str
    sector: str
    state: OperatingState = OperatingState.NORMAL
    active_failures: list[FailureType] = field(default_factory=list)
    kpis: dict[str, float] = field(default_factory=dict)

    @property
    def sector_name(self) -> str:
        return SECTOR_DISPLAY_NAMES[self.sector]

    def _apply(self, name: str, proposed: float) -> None:
        self.kpis[name] = compose_kpi(name, self.kpis[name], proposed)

    def regenerate(self, rng: random.Random) -> None:
        state_key = self.state.value
        self.kpis = {
            name: _sample(*ranges[state_key], rng)
            for name, ranges in KPI_RANGES.items()
        }

        # Soft drift first (or any order): compose with min/max so hard failures still win.
        if FailureType.SOFT_KPI_DRIFT in self.active_failures:
            for name, band in SOFT_DRIFT_RANGES.items():
                self._apply(name, _sample(*band, rng))

        if FailureType.RF_INTERFERENCE in self.active_failures:
            self._apply("rsrp_dbm", _sample(*KPI_RANGES["rsrp_dbm"]["critical"], rng))
            self._apply("rsrq_db", _sample(*KPI_RANGES["rsrq_db"]["critical"], rng))
            self._apply("sinr_db", _sample(*KPI_RANGES["sinr_db"]["critical"], rng))

        if FailureType.BACKHAUL_DEGRADATION in self.active_failures:
            self._apply("latency_ms", _sample(*KPI_RANGES["latency_ms"]["critical"], rng))
            self._apply(
                "packet_loss_pct",
                _sample(*KPI_RANGES["packet_loss_pct"]["critical"], rng),
            )
            self._apply(
                "dl_throughput_mbps",
                _sample(*KPI_RANGES["dl_throughput_mbps"]["degraded"], rng),
            )
            self._apply(
                "ul_throughput_mbps",
                _sample(*KPI_RANGES["ul_throughput_mbps"]["degraded"], rng),
            )

        if FailureType.CAPACITY_CONGESTION in self.active_failures:
            self._apply(
                "active_users",
                _sample(*KPI_RANGES["active_users"]["critical"], rng),
            )
            self._apply(
                "handover_success_pct",
                _sample(*KPI_RANGES["handover_success_pct"]["degraded"], rng),
            )
            self._apply(
                "dl_throughput_mbps",
                _sample(*KPI_RANGES["dl_throughput_mbps"]["degraded"], rng),
            )
            self._apply(
                "ul_throughput_mbps",
                _sample(*KPI_RANGES["ul_throughput_mbps"]["degraded"], rng),
            )

        # Cell outage dominates: absolute override after compositional faults so
        # congestion/user semantics cannot keep a "busy" cell that is actually down.
        if FailureType.CELL_OUTAGE in self.active_failures:
            self.kpis["availability_pct"] = rng.uniform(0.0, 2.0)
            self.kpis["dl_throughput_mbps"] = rng.uniform(0.0, 0.5)
            self.kpis["ul_throughput_mbps"] = rng.uniform(0.0, 0.2)
            self.kpis["active_users"] = rng.uniform(0.0, 3.0)
            self.kpis["handover_success_pct"] = rng.uniform(0.0, 10.0)
            self.kpis["packet_loss_pct"] = rng.uniform(50.0, 100.0)
            self.kpis["latency_ms"] = rng.uniform(500.0, 2000.0)
            self.kpis["rsrp_dbm"] = _sample(*KPI_RANGES["rsrp_dbm"]["critical"], rng)
            self.kpis["rsrq_db"] = _sample(*KPI_RANGES["rsrq_db"]["critical"], rng)
            self.kpis["sinr_db"] = _sample(*KPI_RANGES["sinr_db"]["critical"], rng)

    def to_model(self) -> SectorKPIs:
        return SectorKPIs(
            site=self.site,
            sector=self.sector,
            sector_name=self.sector_name,
            state=self.state,
            rsrp_dbm=round(self.kpis["rsrp_dbm"], 2),
            rsrq_db=round(self.kpis["rsrq_db"], 2),
            sinr_db=round(self.kpis["sinr_db"], 2),
            dl_throughput_mbps=round(self.kpis["dl_throughput_mbps"], 2),
            ul_throughput_mbps=round(self.kpis["ul_throughput_mbps"], 2),
            packet_loss_pct=round(self.kpis["packet_loss_pct"], 3),
            latency_ms=round(self.kpis["latency_ms"], 2),
            availability_pct=round(self.kpis["availability_pct"], 3),
            active_users=round(self.kpis["active_users"], 1),
            handover_success_pct=round(self.kpis["handover_success_pct"], 2),
            active_failures=list(self.active_failures),
        )


class NetworkSimulator:
    """In-memory Private LTE network with 3 sites x 3 sectors."""

    FAILURE_STATE_MAP: dict[FailureType, OperatingState] = {
        FailureType.RF_INTERFERENCE: OperatingState.DEGRADED,
        FailureType.BACKHAUL_DEGRADATION: OperatingState.DEGRADED,
        FailureType.CELL_OUTAGE: OperatingState.CRITICAL,
        FailureType.CAPACITY_CONGESTION: OperatingState.DEGRADED,
        # Soft drift alone stays normal so static warning rules do not trip early.
        FailureType.SOFT_KPI_DRIFT: OperatingState.NORMAL,
    }

    def __init__(self, seed: int | None = 42) -> None:
        self._rng = random.Random(seed)
        self.sectors: dict[tuple[str, str], SectorRuntime] = {
            (site, sector): SectorRuntime(site=site, sector=sector)
            for site in SITES
            for sector in SECTORS
        }
        self.refresh()

    def refresh(self) -> None:
        for sector in self.sectors.values():
            sector.regenerate(self._rng)

    def sync_from_incidents(self, store: IncidentStore) -> None:
        for key, sector in self.sectors.items():
            site, sector_id = key
            failures = store.failures_for(site, sector_id)
            sector.active_failures = failures
            sector.state = self._derive_state(failures)
        self.refresh()

    @classmethod
    def _derive_state(cls, failures: list[FailureType]) -> OperatingState:
        if not failures:
            return OperatingState.NORMAL
        worst = OperatingState.NORMAL
        for failure in failures:
            candidate = cls.FAILURE_STATE_MAP[failure]
            if STATE_SEVERITY[candidate.value] > STATE_SEVERITY[worst.value]:
                worst = candidate
        return worst

    def list_sectors(self) -> list[SectorKPIs]:
        return [sector.to_model() for sector in self.sectors.values()]

    def topology(self) -> dict:
        return {
            "sites": [
                {
                    "id": site,
                    "sectors": [
                        {
                            "id": sector,
                            "name": SECTOR_DISPLAY_NAMES[sector],
                        }
                        for sector in SECTORS
                    ],
                }
                for site in SITES
            ]
        }

    def get_sector(self, site: str, sector: str) -> SectorRuntime | None:
        return self.sectors.get((site, sector))
