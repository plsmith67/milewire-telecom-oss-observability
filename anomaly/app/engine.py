"""Evaluation loop that queries Prometheus and updates anomaly lifecycle."""

from __future__ import annotations

import logging
import time
from typing import Any

from app.config import Settings
from app.detector import DETECTOR_VERSION, dispersion, evaluate_with_stats
from app.metrics import MetricsRegistry, anomaly_to_dict
from app.prom_client import PrometheusClient, Sample
from app.publisher import IncidentPublisher
from app.store import AnomalyStore, fingerprint, utc_now

logger = logging.getLogger(__name__)


def split_baseline_observation(
    samples: list[Sample],
    *,
    guard_sec: int,
) -> tuple[list[Sample], Sample] | None:
    """Separate reference baseline from the current observation.

    Baseline uses only samples at least ``guard_sec`` before the observation
    timestamp so the observation (and recent drift) cannot enter its own
    reference window.
    """
    if not samples:
        return None
    latest = samples[-1]
    baseline_end = latest.ts - max(0, guard_sec)
    baseline = [s for s in samples if s.ts <= baseline_end]
    if not baseline:
        return None
    return baseline, latest


class AnomalyEngine:
    def __init__(
        self,
        settings: Settings,
        store: AnomalyStore,
        prom: PrometheusClient,
        publisher: IncidentPublisher,
        metrics: MetricsRegistry,
    ) -> None:
        self.settings = settings
        self.store = store
        self.prom = prom
        self.publisher = publisher
        self.metrics = metrics

    def run_once(self) -> dict[str, Any]:
        published: list[str] = []
        evaluated = 0
        with self.store._lock:
            for site in self.settings.sites:
                for sector in self.settings.sectors:
                    for kpi_name, kpi in self.settings.kpis.items():
                        result = self._evaluate_series(site, sector, kpi_name, kpi)
                        evaluated += 1
                        if result and result.get("publish"):
                            published.append(result["id"])
            self.store.commit()
        for anomaly_id in published:
            row = self.store.get(anomaly_id)
            if row is None:
                continue
            try:
                self.publisher.publish(self._webhook_payload(row))
            except Exception:  # noqa: BLE001 - keep eval loop alive
                logger.exception("Failed to publish anomaly %s", anomaly_id)
                self.metrics.eval_total.labels(result="publish_error").inc()
        self.metrics.refresh(self.store)
        self.metrics.eval_total.labels(result="ok").inc()
        return {"evaluated": evaluated, "published": len(published), "anomalies": published}

    def _evaluate_series(self, site: str, sector: str, kpi_name: str, kpi) -> dict[str, Any] | None:
        key = fingerprint(site, sector, kpi_name)
        try:
            samples = self.prom.query_range_values(
                kpi_name,
                site,
                sector,
                self.settings.baseline_window,
                lookback_extra_sec=self.settings.baseline_guard_sec,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Prometheus query failed for %s", key)
            return None

        split = split_baseline_observation(samples, guard_sec=self.settings.baseline_guard_sec)
        if split is None:
            return None
        baseline_samples, latest = split

        age = time.time() - latest.ts
        if age > self.settings.stale_sec:
            return None

        window = [{"ts": s.ts, "value": s.value} for s in samples[-min(len(samples), 40) :]]
        open_row = self.store.find_open(site, sector, kpi_name)

        if len(baseline_samples) < self.settings.min_samples and open_row is None:
            return None

        if open_row is not None and open_row["lifecycle_state"] in {
            "warming_up",
            "active",
            "recovering",
        }:
            # Freeze clean baseline while the series is degraded.
            mid = float(open_row["baseline_median"])
            sigma = float(open_row["baseline_dispersion"])
        else:
            if len(baseline_samples) < self.settings.min_samples:
                return None
            mid, sigma = dispersion(
                [s.value for s in baseline_samples],
                kpi.mad_epsilon,
            )

        detection = evaluate_with_stats(
            baseline_median=mid,
            baseline_dispersion=sigma,
            observed=latest.value,
            direction=kpi.direction,
            threshold=kpi.score_threshold,
            ewma_prev=self.store.get_ewma(key),
            alpha=self.settings.ewma_alpha,
        )
        self.store.set_ewma(key, detection.ewma_value)

        publish = False
        detector_version = self.settings.detector_version or DETECTOR_VERSION

        if detection.is_candidate:
            hits = self.store.hit_streak(key) + 1
            self.store.set_hit_streak(key, hits)
            if open_row is None:
                state = "active" if hits >= self.settings.persist_count else "warming_up"
                anomaly_id = self.store.insert(
                    site=site,
                    sector=sector,
                    kpi=kpi_name,
                    observed=detection.observed,
                    baseline_median=detection.baseline_median,
                    baseline_dispersion=detection.baseline_dispersion,
                    robust_z=detection.robust_z,
                    ewma_score=detection.ewma_score,
                    combined_score=detection.combined_score,
                    direction=kpi.direction,
                    lifecycle_state=state if state == "active" else "warming_up",
                    detector_version=detector_version,
                    sample_window=window,
                )
                if state != "active":
                    self.store.add_event(
                        anomaly_id,
                        "suppressed_transient",
                        {"hits": hits, "score": detection.combined_score},
                    )
                else:
                    self.store.add_event(
                        anomaly_id,
                        "persisted_active",
                        {"hits": hits, "score": detection.combined_score},
                    )
                    publish = True
                return {"id": anomaly_id, "publish": publish}

            previous = open_row["lifecycle_state"]
            new_state = previous
            if previous == "warming_up" and hits >= self.settings.persist_count:
                new_state = "active"
                self.store.add_event(open_row["id"], "persisted_active", {"hits": hits})
                publish = True
            elif previous == "recovering" and hits >= self.settings.persist_count:
                new_state = "active"
                publish = True
            elif previous == "active":
                publish = False

            if previous == "warming_up" and hits < self.settings.persist_count:
                self.store.add_event(
                    open_row["id"],
                    "suppressed_transient",
                    {"hits": hits, "score": detection.combined_score},
                )

            self.store.update(
                open_row["id"],
                observed=detection.observed,
                # Keep frozen baseline stats on the open record.
                baseline_median=mid,
                baseline_dispersion=sigma,
                robust_z=detection.robust_z,
                ewma_score=detection.ewma_score,
                combined_score=detection.combined_score,
                lifecycle_state=new_state,
                sample_window=window,
            )
            if new_state == "active" and previous == "active":
                self.store.add_event(
                    open_row["id"],
                    "score_updated",
                    {"score": detection.combined_score},
                )
                publish = True
            return {"id": open_row["id"], "publish": publish}

        # Non-candidate path
        misses = self.store.miss_streak(key) + 1
        self.store.set_miss_streak(key, misses)
        if open_row is None:
            return None

        previous = open_row["lifecycle_state"]
        new_state = previous
        cleared_at = None
        publish = False
        recover_threshold = kpi.score_threshold * 0.7
        if previous == "active" and detection.combined_score < recover_threshold:
            new_state = "recovering"
            self.store.add_event(open_row["id"], "recovering", {"misses": misses})
            publish = True
        elif previous in {"active", "recovering"} and misses >= self.settings.recover_count:
            new_state = "cleared"
            cleared_at = utc_now()
            self.store.add_event(open_row["id"], "cleared", {"misses": misses})
            publish = True
        elif previous == "warming_up" and misses >= self.settings.recover_count:
            new_state = "cleared"
            cleared_at = utc_now()
            self.store.add_event(open_row["id"], "cleared", {"reason": "warmup_aborted"})
            publish = False

        self.store.update(
            open_row["id"],
            observed=detection.observed,
            baseline_median=mid,
            baseline_dispersion=sigma,
            robust_z=detection.robust_z,
            ewma_score=detection.ewma_score,
            combined_score=detection.combined_score,
            lifecycle_state=new_state,
            cleared_at=cleared_at,
            sample_window=window,
        )
        return {"id": open_row["id"], "publish": publish}

    def _webhook_payload(self, row) -> dict[str, Any]:
        payload = anomaly_to_dict(row, include_window=True)
        payload["status"] = row["lifecycle_state"]
        payload["domain"] = self.settings.kpis[row["kpi"]].domain
        payload["starts_at"] = row["first_detected"]
        payload["ends_at"] = row["cleared_at"]
        return payload
