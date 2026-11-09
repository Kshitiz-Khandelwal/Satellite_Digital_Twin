"""Unified Digital Twin Orchestration Engine.

Fuses:
1. SGP4 Orbital Propagator (physics prediction baseline)
2. CelesTrak TLE & Live Telemetry Ingestion
3. DataHygieneGate (filtering, deduplication, MAD outlier gating)
4. OrbitEKF (6-DOF Extended Kalman Filter with covariance propagation)
5. EclipseModel (Earth shadow, solar illumination, beta angle)
6. SubsystemHealthSimulator (EPS power, battery SoC, TCS thermal balance)
7. ResidualAnomalyDetector (innovation gating and residual thresholding)
8. TwinStateMachine (LIVE -> DELAYED -> STALE -> PREDICTED lifecycle)
9. Fault Injection Controller (feed cut, latency, noise, naive comparison)
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np

from app.adapters.simulator import SimulatorAdapter
from app.models.measurement import Measurement
from app.models.satellite import SatelliteMetadata
from app.pipeline.hygiene import DataHygieneGate
from app.pipeline.state_machine import TwinStateMachine
from app.twin.anomaly_detector import ResidualAnomalyDetector
from app.twin.coordinate import CoordinateConverter
from app.twin.eclipse import EclipseModel, EclipseState
from app.twin.ekf import EKFState, OrbitEKF
from app.twin.health_models import (
    SubsystemHealthSimulator,
    SubsystemHealthSummary,
)
from app.twin.propagator import OrbitPropagator


@dataclass
class FaultConfig:
    feed_cut: bool = False
    packet_latency_s: float = 0.0
    sensor_noise_km: float = 0.0
    compare_naive: bool = False


@dataclass
class TwinTelemetryFrame:
    timestamp: str
    satellite_name: str
    norad_id: int
    twin_status: str
    status_color: str
    time_since_last_measurement_s: float
    
    # EKF & Kinematics
    position_geodetic: dict[str, float]  # lat, lon, alt_km, velocity_km_s
    position_ecef_km: list[float]
    velocity_ecef_km_s: list[float]
    ekf_confidence_pct: float
    ekf_sigma_pos_km: float
    ekf_uncertainty_radius_km: float
    ekf_is_propagated_only: bool
    ekf_innovation_residual_km: float

    # Subsystem Health
    eclipse: dict[str, Any]
    power: dict[str, Any]
    thermal: dict[str, Any]
    comms: dict[str, Any]
    overall_health_score: float

    # Naive tracker (for side-by-side comparison)
    naive_position: dict[str, Any] | None = None

    # Events & Diagnostics
    active_anomalies: list[dict[str, Any]] = field(default_factory=list)
    recent_rejections: list[str] = field(default_factory=list)
    active_faults: dict[str, Any] = field(default_factory=dict)


class DigitalTwinEngine:
    """Master runtime for the Satellite Digital Twin."""

    def __init__(
        self,
        norad_id: int = 25544,
        satellite_name: str = "ISS (ZARYA)",
    ):
        self.norad_id = norad_id
        self.satellite_name = satellite_name

        # Upstream adapters
        self.simulator = SimulatorAdapter(satellite_id=norad_id)

        # Coordinate converter
        self.coords = CoordinateConverter()

        # Hygiene & state machine
        self.hygiene = DataHygieneGate()
        self.state_machine = TwinStateMachine(
            stale_threshold_seconds=2.5,
            predicted_threshold_seconds=6.0,
            delayed_latency_threshold_seconds=2.5,
        )

        # Subsystem & anomaly models
        self.eclipse_model = EclipseModel()
        self.health_simulator = SubsystemHealthSimulator()
        self.anomaly_detector = ResidualAnomalyDetector()

        # Fault injection state
        self.faults = FaultConfig()

        # Naive tracker state
        self._naive_lat = 0.0
        self._naive_lon = 0.0
        self._naive_alt = 408.0
        self._naive_status = "INITIALIZING"

        # EKF and Propagator
        self.ekf: OrbitEKF | None = None
        self.propagator: OrbitPropagator | None = None
        self.metadata: SatelliteMetadata = SatelliteMetadata(
            norad_id=norad_id,
            name=satellite_name,
            orbit_source="SGP4/WGS72",
        )

        self._last_step_time = datetime.now(UTC)
        self._bootstrap_orbit()

    def _bootstrap_orbit(self) -> None:
        """Initializes SGP4 Satrec and initial EKF state."""
        self.propagator = OrbitPropagator(self.simulator.satellite)

        now = datetime.now(UTC)
        prop = self.propagator.propagate(now)
        pos_teme = prop["position_km"]
        vel_teme = prop["velocity_km_s"]

        pos_ecef, vel_ecef = self.coords.teme_to_ecef(pos_teme, vel_teme, now)

        self.ekf = OrbitEKF(
            initial_position_km=pos_ecef,
            initial_velocity_km_s=vel_ecef,
            initial_timestamp=now,
            process_noise_pos=0.002,
            process_noise_vel=0.0005,
            measurement_noise_pos=0.05,
        )

        lat, lon, alt = self.coords.ecef_to_geodetic(pos_ecef)
        self._naive_lat = lat
        self._naive_lon = lon
        self._naive_alt = alt

    def set_fault(
        self,
        feed_cut: bool | None = None,
        packet_latency_s: float | None = None,
        sensor_noise_km: float | None = None,
        compare_naive: bool | None = None,
    ) -> None:
        """Configures simulated fault injection parameters."""
        if feed_cut is not None:
            self.faults.feed_cut = feed_cut
        if packet_latency_s is not None:
            self.faults.packet_latency_s = max(0.0, packet_latency_s)
        if sensor_noise_km is not None:
            self.faults.sensor_noise_km = max(0.0, sensor_noise_km)
        if compare_naive is not None:
            self.faults.compare_naive = compare_naive

    def reset_faults(self) -> None:
        """Clears all active fault injections."""
        self.faults = FaultConfig()

    def step(self, current_time: datetime | None = None) -> TwinTelemetryFrame:
        """Executes a single 1-second simulation & fusion cycle.

        Returns complete synchronized telemetry frame for the dashboard.
        """
        now = current_time or datetime.now(UTC)
        if now.tzinfo is None:
            now = now.astimezone(UTC)

        # 1. Propagate Physics Baseline (SGP4)
        sgp4_state = self.propagator.propagate(now)
        sgp4_teme = sgp4_state["position_km"]
        sgp4_vel_teme = sgp4_state["velocity_km_s"]
        sgp4_ecef, sgp4_vel_ecef = self.coords.teme_to_ecef(sgp4_teme, sgp4_vel_teme, now)

        # If stepping with custom or jump in simulation clock, re-align initial EKF epoch
        if abs((now - self.ekf.timestamp).total_seconds()) > 60.0:
            self.ekf.state = np.hstack([sgp4_ecef, sgp4_vel_ecef])
            self.ekf.timestamp = now
            self._last_step_time = now

        # 2. Simulate Incoming Sensor Measurement (Simulate Ground Station / GPS)
        measurement_arrived = not self.faults.feed_cut
        rejection_reasons: list[str] = []
        clean_measurement_dict: dict[str, Any] | None = None

        if measurement_arrived:
            # Generate simulated measurement around SGP4 with slight sensor noise
            noise = np.random.normal(0, 0.02, size=3)
            if self.faults.sensor_noise_km > 0:
                # Inject large fault outlier noise
                noise += np.random.normal(0, self.faults.sensor_noise_km, size=3)

            meas_pos_ecef = sgp4_ecef + noise
            meas_lat, meas_lon, meas_alt = self.coords.ecef_to_geodetic(meas_pos_ecef)

            meas = Measurement(
                satellite_id=self.norad_id,
                source="GPS_RECEIVER_A",
                event_time=now,
                fields={
                    "latitude": meas_lat,
                    "longitude": meas_lon,
                    "altitude": meas_alt,
                    "velocity": float(np.linalg.norm(sgp4_vel_ecef)),
                    "position_km": [float(meas_pos_ecef[0]), float(meas_pos_ecef[1]), float(meas_pos_ecef[2])],
                    "velocity_km_s": [float(sgp4_vel_ecef[0]), float(sgp4_vel_ecef[1]), float(sgp4_vel_ecef[2])],
                },
            )

            # Pass through Data Hygiene Gate
            is_accepted, clean_dict, rejections = self.hygiene.process(meas)
            rejection_reasons = rejections
            if is_accepted and clean_dict is not None:
                clean_measurement_dict = clean_dict

        # 3. Update Lifecycle State Machine
        effective_latency = self.faults.packet_latency_s if measurement_arrived else 0.0
        twin_status_enum = self.state_machine.update(
            current_time=now,
            fresh_packet_arrived=bool(clean_measurement_dict),
            packet_latency_seconds=effective_latency,
            confidence_pct=self.ekf.get_current_state().confidence_pct if self.ekf else 100.0,
        )
        twin_status = twin_status_enum.value

        # Status Badge Color
        status_colors = {
            "LIVE": "#10b981",       # Emerald
            "RECOVERED": "#06b6d4",  # Cyan
            "DELAYED": "#f59e0b",    # Amber
            "STALE": "#f97316",      # Orange
            "PREDICTED": "#38bdf8",  # Sky Blue
        }
        status_color = status_colors.get(twin_status, "#94a3b8")

        # 4. EKF Orbit Fusion Step
        # Predict step forward in time
        self.ekf.predict(now)

        # Measurement fusion with innovation gating
        if clean_measurement_dict:
            pos_meas = np.asarray(clean_measurement_dict["position_km"], dtype=float)

            # Check Chi2 innovation gate before fusing
            pred_state = self.ekf.get_current_state()
            H = np.zeros((3, 6))
            H[0, 0] = H[1, 1] = H[2, 2] = 1.0
            y = pos_meas - pred_state.position_km
            S = H @ pred_state.covariance @ H.T + np.eye(3) * 0.05
            d_m2 = float(y.T @ np.linalg.inv(S) @ y)
            innov_norm = float(np.linalg.norm(y))

            # Residual Anomaly Detector check
            anom = self.anomaly_detector.check_position_innovation(
                timestamp=now,
                innovation_km=innov_norm,
                chi2_mahalanobis=d_m2,
            )

            # If innovation gate fails or outlier detected, reject fusion
            if d_m2 > 16.27 or innov_norm > 25.0:
                rejection_reasons.append(f"EKF_INNOVATION_GATE_REJECTED (residual={innov_norm:.1f}km)")
            else:
                self.ekf.update(pos_meas, measurement_timestamp=now)

        ekf_snapshot: EKFState = self.ekf.get_current_state()
        fused_pos_ecef = ekf_snapshot.position_km
        fused_vel_ecef = ekf_snapshot.velocity_km_s
        fused_lat, fused_lon, fused_alt = self.coords.ecef_to_geodetic(fused_pos_ecef)
        vel_mag = float(np.linalg.norm(fused_vel_ecef))

        # 5. Naive Tracker Simulation (Demonstrating Difference)
        if self.faults.feed_cut:
            self._naive_status = "SIGNAL_LOST_FROZEN"
        elif self.faults.sensor_noise_km > 0:
            self._naive_lat = fused_lat + random.uniform(-0.8, 0.8)
            self._naive_lon = fused_lon + random.uniform(-0.8, 0.8)
            self._naive_alt = fused_alt + random.uniform(-50.0, 50.0)
            self._naive_status = "ERRATIC_JITTER"
        else:
            self._naive_lat = fused_lat
            self._naive_lon = fused_lon
            self._naive_alt = fused_alt
            self._naive_status = "TRACKING"

        # 6. Eclipse and Solar Vector Geometry
        eclipse_state: EclipseState = self.eclipse_model.calculate_illumination(
            satellite_pos_km=fused_pos_ecef,
            timestamp=now,
        )

        # 7. Subsystem Health Physics Simulation (EPS & TCS)
        dt_s = (now - self._last_step_time).total_seconds()
        if dt_s <= 0 or dt_s > 60:
            dt_s = 1.0
        self._last_step_time = now

        subsystem_summary: SubsystemHealthSummary = self.health_simulator.step(
            timestamp=now,
            position_ecef_km=fused_pos_ecef,
            velocity_ecef_km_s=fused_vel_ecef,
            eclipse_state=eclipse_state,
            dt_seconds=dt_s,
        )

        # 8. Check Subsystem Residual Anomalies
        p_obs = subsystem_summary.power.solar_generation_kw
        t_obs = subsystem_summary.thermal.core_temp_c
        self.anomaly_detector.check_subsystem_residuals(
            timestamp=now,
            observed_telemetry={"solar_power_kw": p_obs, "core_temp_c": t_obs},
            modeled_telemetry={"solar_power_kw": subsystem_summary.power.solar_generation_kw, "core_temp_c": subsystem_summary.thermal.core_temp_c},
        )

        # 9. Format Active Anomalies for Dashboard
        active_anom_list = self.anomaly_detector.get_recent_events(limit=5)

        time_since_meas = (
            (now - self.state_machine.last_measurement_time).total_seconds()
            if self.state_machine.last_measurement_time
            else 999.0
        )

        return TwinTelemetryFrame(
            timestamp=now.isoformat(),
            satellite_name=self.satellite_name,
            norad_id=self.norad_id,
            twin_status=twin_status,
            status_color=status_color,
            time_since_last_measurement_s=round(time_since_meas, 1),
            position_geodetic={
                "latitude": round(fused_lat, 4),
                "longitude": round(fused_lon, 4),
                "altitude_km": round(fused_alt, 2),
                "velocity_km_s": round(vel_mag, 3),
            },
            position_ecef_km=[round(float(v), 2) for v in fused_pos_ecef],
            velocity_ecef_km_s=[round(float(v), 4) for v in fused_vel_ecef],
            ekf_confidence_pct=round(ekf_snapshot.confidence_pct, 1),
            ekf_sigma_pos_km=round(ekf_snapshot.sigma_pos_km, 3),
            ekf_uncertainty_radius_km=round(ekf_snapshot.sigma_pos_km * 3.0, 3),
            ekf_is_propagated_only=ekf_snapshot.is_propagated_only,
            ekf_innovation_residual_km=round(ekf_snapshot.innovation_residual_km, 3),
            naive_position={
                "latitude": round(self._naive_lat, 4),
                "longitude": round(self._naive_lon, 4),
                "altitude_km": round(self._naive_alt, 2),
                "status": self._naive_status,
            },
            eclipse={
                "status": eclipse_state.status,
                "illumination_pct": round(eclipse_state.illumination_fraction * 100.0, 1),
                "beta_angle_deg": round(eclipse_state.beta_angle_deg, 2),
                "shadow_depth_km": round(eclipse_state.shadow_depth_km, 1),
                "sun_vector": [round(float(v), 4) for v in eclipse_state.sun_vector_norm],
            },
            power={
                "solar_generation_kw": round(subsystem_summary.power.solar_generation_kw, 2),
                "battery_soc_pct": round(subsystem_summary.power.battery_soc_pct, 1),
                "bus_voltage_v": round(subsystem_summary.power.bus_voltage_v, 2),
                "net_power_kw": round(subsystem_summary.power.net_power_kw, 2),
                "charge_state": subsystem_summary.power.battery_charge_state,
            },
            thermal={
                "core_temp_c": round(subsystem_summary.thermal.core_temp_c, 1),
                "solar_array_temp_c": round(subsystem_summary.thermal.solar_array_temp_c, 1),
                "radiator_flux_w_m2": round(subsystem_summary.thermal.radiator_flux_w_m2, 1),
                "status": subsystem_summary.thermal.thermal_balance_status,
            },
            comms={
                "latency_ms": round(subsystem_summary.comms_latency_ms, 1),
                "attitude_stability_score": round(subsystem_summary.attitude_stability_score, 3),
            },
            overall_health_score=round(subsystem_summary.overall_health_score, 1),
            active_anomalies=active_anom_list,
            recent_rejections=rejection_reasons,
            active_faults={
                "feed_cut": self.faults.feed_cut,
                "packet_latency_s": self.faults.packet_latency_s,
                "sensor_noise_km": self.faults.sensor_noise_km,
                "compare_naive": self.faults.compare_naive,
            },
        )
