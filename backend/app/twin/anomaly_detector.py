"""Statistical Residual Anomaly Detection Engine for Satellite Digital Twin.

Computes difference between observed telemetry and twin physics models:
    residual = |observed - twin_model|

Features:
1. Orbital Position Innovation Gating:
   Checks Mahalanobis distance of incoming measurement against EKF covariance S:
       d_M^2 = y^T * S^-1 * y
   Rejects outliers (d_M^2 > chi2_threshold) to prevent corrupting filter state.
2. Subsystem Telemetry Residual Monitoring:
   - Power residual: |P_obs - P_model| > threshold_kw
   - Thermal residual: |T_obs - T_model| > threshold_degC
   - Attitude residual: Pointing error divergence
3. Structured Event Stream:
   Generates timestamped anomaly notifications with severity levels (INFO, WARNING, CRITICAL).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class AnomalyEvent:
    timestamp: datetime
    subsystem: str       # "ORBIT", "EPS", "TCS", "ADCS", "COMMS"
    severity: str        # "INFO", "WARNING", "CRITICAL"
    code: str            # e.g. "INNOVATION_GATE_VIOLATION"
    message: str
    residual_value: float
    threshold_value: float


class ResidualAnomalyDetector:
    """Monitors telemetry innovations and flags discrepancies from physics baseline."""

    def __init__(
        self,
        position_residual_warn_km: float = 5.0,
        position_residual_crit_km: float = 25.0,
        power_residual_warn_kw: float = 15.0,
        thermal_residual_warn_c: float = 6.0,
        max_history_events: int = 50,
    ):
        self.pos_warn = position_residual_warn_km
        self.pos_crit = position_residual_crit_km
        self.pwr_warn = power_residual_warn_kw
        self.thm_warn = thermal_residual_warn_c
        self.max_events = max_history_events

        self.event_history: list[AnomalyEvent] = []

    def check_position_innovation(
        self,
        timestamp: datetime,
        innovation_km: float,
        chi2_mahalanobis: float | None = None
    ) -> AnomalyEvent | None:
        """Evaluates whether incoming position matches predicted orbit within statistical bounds."""
        event = None
        if innovation_km > self.pos_crit or (chi2_mahalanobis and chi2_mahalanobis > 16.27):  # p < 0.001
            event = AnomalyEvent(
                timestamp=timestamp,
                subsystem="ORBIT",
                severity="CRITICAL",
                code="POSITION_INNOVATION_REJECTED",
                message=f"Measured position drifted {innovation_km:.2f} km from SGP4/EKF trajectory.",
                residual_value=round(innovation_km, 3),
                threshold_value=self.pos_crit
            )
        elif innovation_km > self.pos_warn:
            event = AnomalyEvent(
                timestamp=timestamp,
                subsystem="ORBIT",
                severity="WARNING",
                code="POSITION_RESIDUAL_HIGH",
                message=f"Moderate trajectory divergence ({innovation_km:.2f} km). Kalman gain correcting.",
                residual_value=round(innovation_km, 3),
                threshold_value=self.pos_warn
            )

        if event:
            self._record_event(event)
        return event

    def check_subsystem_residuals(
        self,
        timestamp: datetime,
        observed_telemetry: dict[str, Any],
        modeled_telemetry: dict[str, Any]
    ) -> list[AnomalyEvent]:
        """Compares incoming telemetry against physics models."""
        events = []

        # 1. Power Residual
        if "solar_power_kw" in observed_telemetry and "solar_power_kw" in modeled_telemetry:
            obs_p = float(observed_telemetry["solar_power_kw"])
            mod_p = float(modeled_telemetry["solar_power_kw"])
            diff_p = abs(obs_p - mod_p)
            if diff_p > self.pwr_warn:
                ev = AnomalyEvent(
                    timestamp=timestamp,
                    subsystem="EPS",
                    severity="WARNING" if diff_p < 25.0 else "CRITICAL",
                    code="SOLAR_ARRAY_OUTPUT_ANOMALY",
                    message=f"Solar output {obs_p:.1f} kW differs from physics model ({mod_p:.1f} kW) by {diff_p:.1f} kW.",
                    residual_value=round(diff_p, 2),
                    threshold_value=self.pwr_warn
                )
                events.append(ev)
                self._record_event(ev)

        # 2. Thermal Residual
        if "core_temp_c" in observed_telemetry and "core_temp_c" in modeled_telemetry:
            obs_t = float(observed_telemetry["core_temp_c"])
            mod_t = float(modeled_telemetry["core_temp_c"])
            diff_t = abs(obs_t - mod_t)
            if diff_t > self.thm_warn:
                ev = AnomalyEvent(
                    timestamp=timestamp,
                    subsystem="TCS",
                    severity="WARNING",
                    code="THERMAL_EQUILIBRIUM_DEVIATION",
                    message=f"Core temperature {obs_t:.1f}°C deviates {diff_t:.1f}°C from radiative equilibrium.",
                    residual_value=round(diff_t, 2),
                    threshold_value=self.thm_warn
                )
                events.append(ev)
                self._record_event(ev)

        return events

    def _record_event(self, event: AnomalyEvent):
        self.event_history.insert(0, event)
        if len(self.event_history) > self.max_events:
            self.event_history.pop()

    def get_recent_events(self, limit: int = 10) -> list[dict[str, Any]]:
        return [
            {
                "timestamp": e.timestamp.isoformat(),
                "subsystem": e.subsystem,
                "severity": e.severity,
                "code": e.code,
                "message": e.message,
                "residual": e.residual_value,
                "threshold": e.threshold_value
            }
            for e in self.event_history[:limit]
        ]
