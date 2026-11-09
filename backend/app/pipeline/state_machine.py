"""Digital Twin Lifecycle State Machine.

Transitions cleanly through operating regimes based on data arrival, freshness,
and Kalman filter uncertainty:
    - LIVE: Fresh packet received within nominal interval (< 2.0s), low covariance.
    - DELAYED: Packet received, but arrival latency > 3.0s.
    - STALE: No packet received for > 2.5s; starting coasting propagation.
    - PREDICTED: Outage > 6.0s; free-running SGP4/EKF propagation with expanding covariance.
    - RECOVERED: Fresh telemetry received after STALE/PREDICTED regime, collapsing covariance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum


class TwinStatus(str, Enum):
    LIVE = "LIVE"
    DELAYED = "DELAYED"
    STALE = "STALE"
    PREDICTED = "PREDICTED"
    RECOVERED = "RECOVERED"


@dataclass
class TwinStateTransition:
    previous_state: TwinStatus
    current_state: TwinStatus
    timestamp: datetime
    reason: str
    confidence_pct: float
    time_since_last_measurement_s: float


class TwinStateMachine:
    """Manages the lifecycle and truth states of the Satellite Digital Twin."""

    def __init__(
        self,
        stale_threshold_seconds: float = 2.5,
        predicted_threshold_seconds: float = 6.0,
        delayed_latency_threshold_seconds: float = 2.5,
    ):
        self.stale_threshold = stale_threshold_seconds
        self.predicted_threshold = predicted_threshold_seconds
        self.delayed_threshold = delayed_latency_threshold_seconds

        self.current_state = TwinStatus.PREDICTED  # Start in PREDICTED until first packet
        self.last_measurement_time: datetime | None = None
        self.last_transition_time = datetime.now(UTC)
        self.recovery_banner_counter = 0

    def update(
        self,
        current_time: datetime,
        fresh_packet_arrived: bool,
        packet_latency_seconds: float = 0.0,
        confidence_pct: float = 100.0,
    ) -> TwinStatus:
        """Evaluates state transition based on current time and data arrival."""
        current_time = current_time.astimezone(UTC)
        prev = self.current_state

        if fresh_packet_arrived:
            is_initial_acquisition = (self.last_measurement_time is None)
            self.last_measurement_time = current_time

            # If recovering from an outage (after having been connected before)
            if prev in (TwinStatus.STALE, TwinStatus.PREDICTED) and not is_initial_acquisition:
                self.current_state = TwinStatus.RECOVERED
                self.recovery_banner_counter = 3  # Display RECOVERED for 3 updates
            elif self.recovery_banner_counter > 0:
                self.recovery_banner_counter -= 1
                if self.recovery_banner_counter == 0:
                    self.current_state = TwinStatus.LIVE
            elif packet_latency_seconds > self.delayed_threshold:
                self.current_state = TwinStatus.DELAYED
            else:
                self.current_state = TwinStatus.LIVE
        else:
            # No fresh packet in this tick: evaluate gap duration
            if self.last_measurement_time is None:
                gap = 999.0
            else:
                gap = (current_time - self.last_measurement_time).total_seconds()

            if gap >= self.predicted_threshold:
                self.current_state = TwinStatus.PREDICTED
            elif gap >= self.stale_threshold:
                self.current_state = TwinStatus.STALE
            elif self.recovery_banner_counter > 0:
                self.recovery_banner_counter -= 1

        if self.current_state != prev:
            self.last_transition_time = current_time

        return self.current_state

    def get_status_info(self, current_time: datetime, confidence_pct: float) -> dict:
        current_time = current_time.astimezone(UTC)
        gap = 0.0
        if self.last_measurement_time is not None:
            gap = max(0.0, (current_time - self.last_measurement_time).total_seconds())

        badge_colors = {
            TwinStatus.LIVE: "#10b981",       # Emerald
            TwinStatus.DELAYED: "#f59e0b",    # Amber
            TwinStatus.STALE: "#f97316",      # Orange
            TwinStatus.PREDICTED: "#06b6d4",  # Cyan
            TwinStatus.RECOVERED: "#a855f7",  # Purple
        }

        descriptions = {
            TwinStatus.LIVE: "Telemetry fused in real time. Covariance optimal.",
            TwinStatus.DELAYED: "High link latency detected; re-ordering buffer active.",
            TwinStatus.STALE: "Telemetry gap detected (>2.5s). Coasting with EKF.",
            TwinStatus.PREDICTED: "Telemetry loss (>6.0s). Free-running SGP4 orbit physics.",
            TwinStatus.RECOVERED: "Connection restored! Kalman filter snapped back.",
        }

        return {
            "status": self.current_state.value,
            "badge_color": badge_colors[self.current_state],
            "description": descriptions[self.current_state],
            "time_since_last_measurement_s": round(gap, 2),
            "confidence_pct": round(confidence_pct, 1),
        }
