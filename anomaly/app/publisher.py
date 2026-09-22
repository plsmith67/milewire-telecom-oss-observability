"""Publish anomaly lifecycle events to the incident service."""

from __future__ import annotations

from typing import Any

import httpx


class IncidentPublisher:
    def __init__(self, webhook_url: str, timeout: float = 10.0) -> None:
        self.webhook_url = webhook_url
        self.timeout = timeout

    def publish(self, anomaly: dict[str, Any]) -> dict[str, Any] | None:
        payload = {"anomalies": [anomaly]}
        response = httpx.post(self.webhook_url, json=payload, timeout=self.timeout)
        response.raise_for_status()
        return response.json()
