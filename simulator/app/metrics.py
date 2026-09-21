"""Prometheus metric registration and updates."""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, generate_latest

from app.failures import IncidentStore
from app.simulator import NetworkSimulator

KPI_METRIC_NAMES = {
    "rsrp_dbm": ("lte_rsrp_dbm", "Reference Signal Received Power in dBm"),
    "rsrq_db": ("lte_rsrq_db", "Reference Signal Received Quality in dB"),
    "sinr_db": ("lte_sinr_db", "Signal to Interference plus Noise Ratio in dB"),
    "dl_throughput_mbps": ("lte_dl_throughput_mbps", "Downlink throughput in Mbps"),
    "ul_throughput_mbps": ("lte_ul_throughput_mbps", "Uplink throughput in Mbps"),
    "packet_loss_pct": ("lte_packet_loss_pct", "Packet loss percentage"),
    "latency_ms": ("lte_latency_ms", "User-plane latency in milliseconds"),
    "availability_pct": ("lte_availability_pct", "Sector availability percentage"),
    "active_users": ("lte_active_users", "Number of active users on the sector"),
    "handover_success_pct": (
        "lte_handover_success_pct",
        "Handover success rate percentage",
    ),
}


class MetricsRegistry:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.gauges: dict[str, Gauge] = {}
        for kpi_key, (metric_name, help_text) in KPI_METRIC_NAMES.items():
            self.gauges[kpi_key] = Gauge(
                metric_name,
                help_text,
                ["site", "sector", "state"],
                registry=self.registry,
            )

        self.active_incidents = Gauge(
            "lte_active_incidents",
            "Number of active injected failures",
            registry=self.registry,
        )
        self.incident_info = Gauge(
            "lte_incident_info",
            "Active incident details (value=1 while active)",
            ["incident_id", "failure_type", "site", "sector", "operating_state"],
            registry=self.registry,
        )
        self.sector_state = Gauge(
            "lte_sector_state",
            "Sector operating state encoded as 0=normal, 1=degraded, 2=critical",
            ["site", "sector", "state"],
            registry=self.registry,
        )

    def update(self, simulator: NetworkSimulator, store: IncidentStore) -> None:
        # Clear label sets that may no longer apply (state changes)
        for gauge in self.gauges.values():
            gauge.clear()
        self.sector_state.clear()
        self.incident_info.clear()

        state_code = {"normal": 0, "degraded": 1, "critical": 2}

        for sector in simulator.sectors.values():
            labels = {
                "site": sector.site,
                "sector": sector.sector,
                "state": sector.state.value,
            }
            for kpi_key, gauge in self.gauges.items():
                gauge.labels(**labels).set(sector.kpis[kpi_key])

            self.sector_state.labels(**labels).set(state_code[sector.state.value])

        self.active_incidents.set(store.count())
        for incident in store.list():
            self.incident_info.labels(
                incident_id=incident.id,
                failure_type=incident.failure_type.value,
                site=incident.site,
                sector=incident.sector or "all",
                operating_state=incident.operating_state.value,
            ).set(1)

    def expose(self) -> tuple[bytes, str]:
        return generate_latest(self.registry), CONTENT_TYPE_LATEST
