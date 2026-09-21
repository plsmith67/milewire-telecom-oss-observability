"""Topology and KPI range configuration for Private LTE simulation."""

from __future__ import annotations

from typing import Final

SITES: Final[tuple[str, ...]] = ("plte-site-101", "plte-site-102", "plte-site-103")
SECTORS: Final[tuple[str, ...]] = ("sector-alpha", "sector-beta", "sector-gamma")

SECTOR_DISPLAY_NAMES: Final[dict[str, str]] = {
    "sector-alpha": "Sector Alpha",
    "sector-beta": "Sector Beta",
    "sector-gamma": "Sector Gamma",
}

# KPI ranges: (min, max) per operating state
KPI_RANGES: Final[dict[str, dict[str, tuple[float, float]]]] = {
    "rsrp_dbm": {
        "normal": (-85.0, -70.0),
        "degraded": (-105.0, -90.0),
        "critical": (-120.0, -110.0),
    },
    "rsrq_db": {
        "normal": (-10.0, -7.0),
        "degraded": (-15.0, -12.0),
        "critical": (-20.0, -18.0),
    },
    "sinr_db": {
        "normal": (15.0, 25.0),
        "degraded": (5.0, 10.0),
        "critical": (-2.0, 3.0),
    },
    "dl_throughput_mbps": {
        "normal": (80.0, 150.0),
        "degraded": (20.0, 50.0),
        "critical": (1.0, 10.0),
    },
    "ul_throughput_mbps": {
        "normal": (20.0, 50.0),
        "degraded": (5.0, 15.0),
        "critical": (0.5, 3.0),
    },
    "packet_loss_pct": {
        "normal": (0.0, 0.5),
        "degraded": (1.0, 5.0),
        "critical": (8.0, 20.0),
    },
    "latency_ms": {
        "normal": (10.0, 25.0),
        "degraded": (40.0, 80.0),
        "critical": (120.0, 250.0),
    },
    "availability_pct": {
        "normal": (99.5, 100.0),
        "degraded": (95.0, 98.0),
        "critical": (0.0, 70.0),
    },
    "active_users": {
        "normal": (20.0, 80.0),
        "degraded": (100.0, 200.0),
        "critical": (250.0, 400.0),
    },
    "handover_success_pct": {
        "normal": (97.0, 99.5),
        "degraded": (85.0, 92.0),
        "critical": (50.0, 75.0),
    },
}

STATE_SEVERITY: Final[dict[str, int]] = {
    "normal": 0,
    "degraded": 1,
    "critical": 2,
}
