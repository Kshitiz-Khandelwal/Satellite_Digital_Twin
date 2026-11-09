"""Interactive Fault Injection Harness for Live Demonstrations.

Enables demonstrating the Digital Twin's resilience under degraded conditions:
1. Cut Feed:
   Completely severs the telemetry stream, forcing the twin into STALE -> PREDICTED free-run SGP4.
2. Packet Delay:
   Artificially stalls telemetry arrival to demonstrate latency tracking and the DELAYED state.
3. Telemetry Noise & Outliers:
   Injects high-amplitude deviations to demonstrate MAD gate rejection and anomaly alerts.
4. Stochastic Packet Loss:
   Drops packets at a defined probability (e.g. 30% loss) to showcase Kalman continuity.
5. Naive Tracker Simulation:
   Runs an illustrative naive tracker in parallel that freezes in place when feed is severed,
   providing a direct side-by-side visual demonstration of Twin superiority!
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np


@dataclass
class FaultConfig:
    sever_feed: bool = False
    packet_loss_pct: float = 0.0          # 0 to 100%
    latency_delay_seconds: float = 0.0    # Additional simulated delay
    inject_outlier_spikes: bool = False   # Introduce rogue 50km spikes
    noise_amplitude_km: float = 0.0       # Gaussian jitter amplitude
    naive_tracker_freeze_on_cut: bool = True


class FaultInjector:
    """Modulates incoming raw telemetry according to active demonstration scenarios."""

    def __init__(self):
        self.config = FaultConfig()
        # State for naive tracker comparison
        self.naive_position_ecef_km: np.ndarray | None = None
        self.naive_status: str = "LIVE"
        self.last_clean_position: np.ndarray | None = None

    def update_config(self, **kwargs) -> dict[str, Any]:
        """Dynamically updates active fault injection settings."""
        for k, v in kwargs.items():
            if hasattr(self.config, k):
                setattr(self.config, k, v)
        return self.get_config_dict()

    def get_config_dict(self) -> dict[str, Any]:
        return {
            "sever_feed": self.config.sever_feed,
            "packet_loss_pct": self.config.packet_loss_pct,
            "latency_delay_seconds": self.config.latency_delay_seconds,
            "inject_outlier_spikes": self.config.inject_outlier_spikes,
            "noise_amplitude_km": self.config.noise_amplitude_km,
            "naive_tracker_freeze_on_cut": self.config.naive_tracker_freeze_on_cut,
        }

    def process_incoming_packet(
        self,
        raw_position_km: np.ndarray,
        raw_velocity_km_s: np.ndarray | None,
        timestamp: datetime
    ) -> tuple[bool, np.ndarray | None, np.ndarray | None, float, list[str]]:
        """Applies configured faults to incoming telemetry packet.

        Returns:
            (packet_delivered, pos_km, vel_km_s, latency_s, applied_fault_tags)
        """
        tags = []
        latency = self.config.latency_delay_seconds

        # 1. Total Feed Severance
        if self.config.sever_feed:
            # Naive tracker freezes at last known point
            self.naive_status = "FEED_SEVERED_FROZEN"
            return False, None, None, latency, ["FEED_SEVERED"]

        # 2. Stochastic Packet Loss
        if self.config.packet_loss_pct > 0.0:
            if random.random() * 100.0 < self.config.packet_loss_pct:
                return False, None, None, latency, [f"PACKET_DROPPED ({self.config.packet_loss_pct}%)"]

        # 3. Telemetry Mutation (Noise / Outliers)
        pos = np.asarray(raw_position_km, dtype=float).copy()
        vel = np.asarray(raw_velocity_km_s, dtype=float).copy() if raw_velocity_km_s is not None else None

        if self.config.noise_amplitude_km > 0.0:
            noise = np.random.normal(0.0, self.config.noise_amplitude_km, size=3)
            pos += noise
            tags.append(f"GAUSSIAN_NOISE_{self.config.noise_amplitude_km}KM")

        if self.config.inject_outlier_spikes:
            # 30% chance of injecting a massive 85km sensor outlier spike on this frame
            if random.random() < 0.35:
                spike = np.array([45.0, -60.0, 30.0])
                pos += spike
                tags.append("CORRUPT_OUTLIER_SPIKE_INJECTED")

        # Update Naive Tracker State (a naive tracker just blindly accepts whatever comes in)
        self.naive_position_ecef_km = pos.copy()
        self.naive_status = "LIVE"

        return True, pos, vel, latency, tags
