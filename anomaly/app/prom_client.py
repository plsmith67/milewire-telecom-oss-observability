"""Prometheus HTTP API client for KPI range queries."""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx


@dataclass
class Sample:
    ts: float
    value: float


def parse_window_seconds(window: str) -> int:
    window = window.strip().lower()
    if window.endswith("m"):
        return int(window[:-1]) * 60
    if window.endswith("h"):
        return int(window[:-1]) * 3600
    if window.endswith("s"):
        return int(window[:-1])
    return int(window)


class PrometheusClient:
    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def healthy(self) -> bool:
        try:
            response = httpx.get(f"{self.base_url}/-/healthy", timeout=self.timeout)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def query_range_values(
        self,
        metric: str,
        site: str,
        sector: str,
        window: str,
        step: str = "15s",
        *,
        lookback_extra_sec: int = 0,
    ) -> list[Sample]:
        # Aggregate away the operating-state label so baselines stay continuous.
        expr = f'avg by (site, sector) ({metric}{{site="{site}",sector="{sector}"}})'
        end = time.time()
        start = end - parse_window_seconds(window) - max(0, lookback_extra_sec)
        response = httpx.get(
            f"{self.base_url}/api/v1/query_range",
            params={"query": expr, "start": start, "end": end, "step": step},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "success":
            return []
        result = payload.get("data", {}).get("result") or []
        if not result:
            return []
        values = result[0].get("values") or []
        samples: list[Sample] = []
        for ts, raw in values:
            try:
                samples.append(Sample(ts=float(ts), value=float(raw)))
            except (TypeError, ValueError):
                continue
        return samples
