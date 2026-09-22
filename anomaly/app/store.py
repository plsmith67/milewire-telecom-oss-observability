"""SQLite persistence for anomaly records."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fingerprint(site: str, sector: str, kpi: str) -> str:
    return f"{site}|{sector}|{kpi}"


class AnomalyStore:
    def __init__(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._ewma: dict[str, float] = {}
        self._hit_streak: dict[str, int] = {}
        self._miss_streak: dict[str, int] = {}
        self._init()

    def _init(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS anomalies (
                id TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                site TEXT NOT NULL,
                sector TEXT NOT NULL,
                kpi TEXT NOT NULL,
                observed_value REAL NOT NULL,
                baseline_median REAL NOT NULL,
                baseline_dispersion REAL NOT NULL,
                robust_z REAL NOT NULL,
                ewma_score REAL NOT NULL,
                combined_score REAL NOT NULL,
                direction TEXT NOT NULL,
                lifecycle_state TEXT NOT NULL,
                first_detected TEXT NOT NULL,
                last_observed TEXT NOT NULL,
                cleared_at TEXT,
                detector_version TEXT NOT NULL,
                sample_window_json TEXT NOT NULL,
                incident_id TEXT
            );
            CREATE TABLE IF NOT EXISTS anomaly_events (
                id TEXT PRIMARY KEY,
                anomaly_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                detail_json TEXT NOT NULL,
                FOREIGN KEY (anomaly_id) REFERENCES anomalies(id)
            );
            CREATE INDEX IF NOT EXISTS idx_anomalies_fp_state
                ON anomalies(fingerprint, lifecycle_state);
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def commit(self) -> None:
        self._conn.commit()

    def get_ewma(self, key: str) -> float | None:
        return self._ewma.get(key)

    def set_ewma(self, key: str, value: float) -> None:
        self._ewma[key] = value

    def hit_streak(self, key: str) -> int:
        return self._hit_streak.get(key, 0)

    def miss_streak(self, key: str) -> int:
        return self._miss_streak.get(key, 0)

    def set_hit_streak(self, key: str, value: int) -> None:
        self._hit_streak[key] = value
        if value > 0:
            self._miss_streak[key] = 0

    def set_miss_streak(self, key: str, value: int) -> None:
        self._miss_streak[key] = value
        if value > 0:
            self._hit_streak[key] = 0

    def find_open(self, site: str, sector: str, kpi: str) -> Optional[sqlite3.Row]:
        fp = fingerprint(site, sector, kpi)
        return self._conn.execute(
            """
            SELECT * FROM anomalies
            WHERE fingerprint = ? AND lifecycle_state IN ('warming_up', 'active', 'recovering')
            ORDER BY first_detected DESC LIMIT 1
            """,
            (fp,),
        ).fetchone()

    def get(self, anomaly_id: str) -> Optional[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM anomalies WHERE id = ?", (anomaly_id,)).fetchone()

    def add_event(self, anomaly_id: str, event_type: str, detail: dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT INTO anomaly_events (id, anomaly_id, event_type, timestamp, detail_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), anomaly_id, event_type, utc_now(), json.dumps(detail)),
        )

    def insert(
        self,
        *,
        site: str,
        sector: str,
        kpi: str,
        observed: float,
        baseline_median: float,
        baseline_dispersion: float,
        robust_z: float,
        ewma_score: float,
        combined_score: float,
        direction: str,
        lifecycle_state: str,
        detector_version: str,
        sample_window: list[dict[str, Any]],
    ) -> str:
        anomaly_id = str(uuid.uuid4())
        now = utc_now()
        self._conn.execute(
            """
            INSERT INTO anomalies (
                id, fingerprint, site, sector, kpi, observed_value, baseline_median,
                baseline_dispersion, robust_z, ewma_score, combined_score, direction,
                lifecycle_state, first_detected, last_observed, cleared_at,
                detector_version, sample_window_json, incident_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL)
            """,
            (
                anomaly_id,
                fingerprint(site, sector, kpi),
                site,
                sector,
                kpi,
                observed,
                baseline_median,
                baseline_dispersion,
                robust_z,
                ewma_score,
                combined_score,
                direction,
                lifecycle_state,
                now,
                now,
                detector_version,
                json.dumps(sample_window),
            ),
        )
        self.add_event(anomaly_id, "opened", {"lifecycle_state": lifecycle_state, "kpi": kpi})
        return anomaly_id

    def update(
        self,
        anomaly_id: str,
        *,
        observed: float | None = None,
        baseline_median: float | None = None,
        baseline_dispersion: float | None = None,
        robust_z: float | None = None,
        ewma_score: float | None = None,
        combined_score: float | None = None,
        lifecycle_state: str | None = None,
        cleared_at: str | None = None,
        sample_window: list[dict[str, Any]] | None = None,
        incident_id: str | None = None,
    ) -> None:
        row = self.get(anomaly_id)
        if row is None:
            return
        self._conn.execute(
            """
            UPDATE anomalies SET
                observed_value = ?,
                baseline_median = ?,
                baseline_dispersion = ?,
                robust_z = ?,
                ewma_score = ?,
                combined_score = ?,
                lifecycle_state = ?,
                last_observed = ?,
                cleared_at = ?,
                sample_window_json = ?,
                incident_id = ?
            WHERE id = ?
            """,
            (
                observed if observed is not None else row["observed_value"],
                baseline_median if baseline_median is not None else row["baseline_median"],
                baseline_dispersion if baseline_dispersion is not None else row["baseline_dispersion"],
                robust_z if robust_z is not None else row["robust_z"],
                ewma_score if ewma_score is not None else row["ewma_score"],
                combined_score if combined_score is not None else row["combined_score"],
                lifecycle_state if lifecycle_state is not None else row["lifecycle_state"],
                utc_now(),
                cleared_at if cleared_at is not None else row["cleared_at"],
                json.dumps(sample_window) if sample_window is not None else row["sample_window_json"],
                incident_id if incident_id is not None else row["incident_id"],
                anomaly_id,
            ),
        )

    def list(
        self,
        *,
        state: str | None = None,
        site: str | None = None,
        kpi: str | None = None,
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list[str] = []
        if state:
            clauses.append("lifecycle_state = ?")
            params.append(state)
        if site:
            clauses.append("site = ?")
            params.append(site)
        if kpi:
            clauses.append("kpi = ?")
            params.append(kpi)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return list(
            self._conn.execute(
                f"SELECT * FROM anomalies {where} ORDER BY last_observed DESC",
                params,
            )
        )

    def recent_for_metrics(self) -> list[sqlite3.Row]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        return list(
            self._conn.execute(
                """
                SELECT * FROM anomalies
                WHERE lifecycle_state IN ('warming_up', 'active', 'recovering')
                   OR (lifecycle_state = 'cleared' AND cleared_at >= ?)
                """,
                (cutoff,),
            )
        )

    def list_events(self, anomaly_id: str) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM anomaly_events WHERE anomaly_id = ? ORDER BY timestamp",
                (anomaly_id,),
            )
        )
