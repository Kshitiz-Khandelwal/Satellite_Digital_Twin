"""Unified Digital Twin Orchestration Engine.

Fuses:
1. SGP4 Orbital Propagator (physics prediction baseline)
2. CelesTrak TLE & Live Telemetry Ingestion / Multi-Satellite Catalog
3. DataHygieneGate (filtering, deduplication, MAD outlier gating)
4. OrbitEKF (6-DOF Extended Kalman Filter with covariance propagation)
5. EclipseModel (Earth shadow, solar illumination, beta angle)
6. SubsystemHealthSimulator (EPS power, battery SoC, TCS thermal balance)
7. ResidualAnomalyDetector (innovation gating and residual thresholding)
8. TwinStateMachine (LIVE -> DELAYED -> STALE -> PREDICTED -> RECOVERED lifecycle)
9. Fault Injection Controller (feed cut, latency, noise, naive comparison)
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np

from app.adapters.simulator import SimulatorAdapter
from app.models.catalog import SatelliteSpec, get_satellite_spec
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

    # Mission orbital metadata
    inclination_deg: float
    orbital_period_min: float
    satellite_description: str

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

    # Comparison & Fault Visualization markers
    naive_position: dict[str, Any] | None = None
    delayed_position: dict[str, Any] | None = None
    rejected_spike: dict[str, Any] | None = None
    orbit_track: list[dict[str, float]] = field(default_factory=list)

    # Events & Diagnostics
    active_anomalies: list[dict[str, Any]] = field(default_factory=list)
    recent_rejections: list[str] = field(default_factory=list)
    active_faults: dict[str, Any] = field(default_factory=dict)


class DigitalTwinEngine:
    """Master runtime for the Satellite Digital Twin."""

    def __init__(
        self,
        norad_id: int = 25544,
        satellite_name: str | None = None,
    ):
        self.spec: SatelliteSpec = get_satellite_spec(norad_id)
        self.norad_id = norad_id
        self.satellite_name = satellite_name or self.spec.name

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
        self.health_simulator = SubsystemHealthSimulator(
            nominal_bus_voltage_v=self.spec.nominal_bus_voltage_v,
            max_solar_power_kw=self.spec.max_solar_power_kw,
            nominal_base_load_kw=self.spec.nominal_base_load_kw,
            battery_capacity_kwh=self.spec.battery_capacity_kwh,
        )
        self.anomaly_detector = ResidualAnomalyDetector()

        # Fault injection state
        self.faults = FaultConfig()

        # Naive tracker state
        self._naive_lat = 0.0
        self._naive_lon = 0.0
        self._naive_alt = self.spec.nominal_altitude_km
        self._naive_status = "INITIALIZING"
        self._feed_cut_timestamp: datetime | None = None

        # EKF and Propagator
        self.ekf: OrbitEKF | None = None
        self.propagator: OrbitPropagator | None = None
        self.metadata: SatelliteMetadata = SatelliteMetadata(
            norad_id=norad_id,
            name=self.satellite_name,
            orbit_source="SGP4/WGS72",
        )

        self._cached_track_norad: int | None = None
        self._cached_track_time: datetime | None = None
        self._cached_track_points: list[dict[str, float]] = []

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

    def select_satellite(self, norad_id: int) -> SatelliteSpec:
        """Hot-swaps the active tracked satellite target and resets physics state."""
        self.spec = get_satellite_spec(norad_id)
        self.norad_id = self.spec.norad_id
        self.satellite_name = self.spec.name
        self.simulator = SimulatorAdapter(satellite_id=self.norad_id)
        self.hygiene = DataHygieneGate()
        self.state_machine = TwinStateMachine(
            stale_threshold_seconds=2.5,
            predicted_threshold_seconds=6.0,
            delayed_latency_threshold_seconds=2.5,
        )
        self.health_simulator = SubsystemHealthSimulator(
            nominal_bus_voltage_v=self.spec.nominal_bus_voltage_v,
            max_solar_power_kw=self.spec.max_solar_power_kw,
            nominal_base_load_kw=self.spec.nominal_base_load_kw,
            battery_capacity_kwh=self.spec.battery_capacity_kwh,
        )
        self.anomaly_detector = ResidualAnomalyDetector()
        self.metadata = SatelliteMetadata(
            norad_id=self.norad_id,
            name=self.satellite_name,
            orbit_source="SGP4/WGS72",
        )
        self._cached_track_norad = None
        self._cached_track_time = None
        self._cached_track_points = []
        self._feed_cut_timestamp = None
        self._bootstrap_orbit()
        return self.spec

    def _compute_orbit_track(self, now: datetime) -> list[dict[str, float]]:
        """Computes a full 360-degree SGP4 orbital ring (90 samples) in ECEF/Geodetic."""
        if (
            self._cached_track_norad == self.norad_id
            and self._cached_track_time is not None
            and abs((now - self._cached_track_time).total_seconds()) < 30.0
            and self._cached_track_points
        ):
            return self._cached_track_points

        points: list[dict[str, float]] = []
        period_s = self.spec.orbital_period_min * 60.0
        samples = 72
        for i in range(samples + 1):
            t = now + timedelta(seconds=(i / samples) * period_s)
            try:
                st = self.propagator.propagate(t)
                pos_ecef, _ = self.coords.teme_to_ecef(st["position_km"], st["velocity_km_s"], t)
                points.append({
                    "x": round(float(pos_ecef[0]), 2),
                    "y": round(float(pos_ecef[1]), 2),
                    "z": round(float(pos_ecef[2]), 2),
                })
            except Exception:
                continue

        self._cached_track_norad = self.norad_id
        self._cached_track_time = now
        self._cached_track_points = points
        return points

    def set_fault(
        self,
        feed_cut: bool | None = None,
        packet_latency_s: float | None = None,
        sensor_noise_km: float | None = None,
        compare_naive: bool | None = None,
    ) -> None:
        """Configures simulated fault injection parameters."""
        if feed_cut is not None:
            if feed_cut and not self.faults.feed_cut:
                self._feed_cut_timestamp = datetime.now(UTC)
            elif not feed_cut:
                self._feed_cut_timestamp = None
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
        self._feed_cut_timestamp = None

    def step(
        self,
        current_time: datetime | None = None,
        outage_override_s: float | None = None,
        include_orbit_track: bool = True,
    ) -> TwinTelemetryFrame:
        """Executes a single simulation & fusion cycle.

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

        # 2. Simulate Incoming Sensor Measurement (Ground Station / GPS)
        measurement_arrived = not self.faults.feed_cut
        rejection_reasons: list[str] = []
        clean_measurement_dict: dict[str, Any] | None = None
        rejected_spike_info: dict[str, Any] | None = None

        if measurement_arrived:
            noise = np.random.normal(0, 0.02, size=3)
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

            # Pass nominal stream through Data Hygiene Gate
            is_accepted, clean_dict, rejections = self.hygiene.process(meas)
            rejection_reasons = list(rejections)
            if is_accepted and clean_dict is not None:
                clean_measurement_dict = clean_dict
            else:
                rejected_spike_info = {
                    "latitude": round(meas_lat, 4),
                    "longitude": round(meas_lon, 4),
                    "altitude_km": round(meas_alt, 2),
                    "error_km": round(float(np.linalg.norm(noise)), 2),
                    "reason": rejections[0] if rejections else "MAD_OUTLIER_REJECTED",
                }

            # When outlier injection is enabled, inject a concurrent rogue GPS spike packet into the MAD & Chi2 gates
            if self.faults.sensor_noise_km > 0:
                direction = np.random.normal(0, 1.0, size=3)
                norm = float(np.linalg.norm(direction)) + 1e-9
                spike_km = max(45.0, float(self.faults.sensor_noise_km)) * random.uniform(1.0, 2.2)
                spike_vec = (direction / norm) * spike_km
                spike_ecef = sgp4_ecef + spike_vec
                s_lat, s_lon, s_alt = self.coords.ecef_to_geodetic(spike_ecef)

                spike_meas = Measurement(
                    satellite_id=self.norad_id,
                    source="GPS_RECEIVER_SPOOF",
                    event_time=now + timedelta(milliseconds=10),
                    fields={
                        "latitude": s_lat,
                        "longitude": s_lon,
                        "altitude": s_alt,
                        "velocity": float(np.linalg.norm(sgp4_vel_ecef)),
                        "position_km": [float(spike_ecef[0]), float(spike_ecef[1]), float(spike_ecef[2])],
                        "velocity_km_s": [float(sgp4_vel_ecef[0]), float(sgp4_vel_ecef[1]), float(sgp4_vel_ecef[2])],
                    },
                )
                spike_accepted, _, spike_rejs = self.hygiene.process(spike_meas)
                reason_str = (
                    spike_rejs[0]
                    if (not spike_accepted and spike_rejs)
                    else f"EKF_INNOVATION_GATE_REJECTED (spike={spike_km:.1f}km > chi2_max)"
                )
                rejection_reasons.append(reason_str)
                self.anomaly_detector.check_position_innovation(
                    timestamp=now,
                    innovation_km=spike_km,
                    chi2_mahalanobis=max(100.0, (spike_km / 0.25) ** 2),
                )
                rejected_spike_info = {
                    "latitude": round(s_lat, 4),
                    "longitude": round(s_lon, 4),
                    "altitude_km": round(s_alt, 2),
                    "error_km": round(spike_km, 2),
                    "reason": reason_str,
                }

        # 3. Handle Stateless Serverless Outage Propagation (if outage_override_s provided)
        if self.faults.feed_cut and outage_override_s is not None and outage_override_s > 0.0:
            # Ensure state machine & EKF reflect the true outage age even on cold serverless lambdas
            self.state_machine.last_measurement_time = now - timedelta(seconds=outage_override_s)
            # Reconstruct exact cut epoch SGP4 state and propagate open-loop for outage_override_s
            t_cut = now - timedelta(seconds=outage_override_s)
            cut_st = self.propagator.propagate(t_cut)
            cut_ecef, cut_vel_ecef = self.coords.teme_to_ecef(cut_st["position_km"], cut_st["velocity_km_s"], t_cut)
            self.ekf.state = np.hstack([cut_ecef, cut_vel_ecef])
            self.ekf.timestamp = t_cut
            self.ekf.P = np.diag([0.05**2, 0.05**2, 0.05**2, 0.005**2, 0.005**2, 0.005**2])
            self._naive_lat, self._naive_lon, self._naive_alt = self.coords.ecef_to_geodetic(cut_ecef)

        # 4. EKF Orbit Fusion Step (Predict forward to now)
        self.ekf.predict(now)

        # Measurement fusion with Chi2 innovation gating
        if clean_measurement_dict:
            pos_meas = np.asarray(clean_measurement_dict["position_km"], dtype=float)
            pred_state = self.ekf.get_current_state()
            H = np.zeros((3, 6))
            H[0, 0] = H[1, 1] = H[2, 2] = 1.0
            y = pos_meas - pred_state.position_km
            S = H @ pred_state.covariance @ H.T + np.eye(3) * 0.05
            d_m2 = float(y.T @ np.linalg.inv(S) @ y)
            innov_norm = float(np.linalg.norm(y))

            self.anomaly_detector.check_position_innovation(
                timestamp=now,
                innovation_km=innov_norm,
                chi2_mahalanobis=d_m2,
            )

            if d_m2 > 16.27 or innov_norm > 25.0:
                msg = f"EKF_INNOVATION_GATE_REJECTED (residual={innov_norm:.1f}km, chi2={d_m2:.1f})"
                rejection_reasons.append(msg)
                lat_m, lon_m, alt_m = self.coords.ecef_to_geodetic(pos_meas)
                rejected_spike_info = {
                    "latitude": round(lat_m, 4),
                    "longitude": round(lon_m, 4),
                    "altitude_km": round(alt_m, 2),
                    "error_km": round(innov_norm, 2),
                    "reason": msg,
                }
            else:
                self.ekf.update(pos_meas, measurement_timestamp=now)

        ekf_snapshot: EKFState = self.ekf.get_current_state()

        # 5. Update Lifecycle State Machine
        effective_latency = self.faults.packet_latency_s if measurement_arrived else 0.0
        twin_status_enum = self.state_machine.update(
            current_time=now,
            fresh_packet_arrived=bool(clean_measurement_dict) and (not self.faults.feed_cut),
            packet_latency_seconds=effective_latency,
            confidence_pct=ekf_snapshot.confidence_pct,
        )
        twin_status = twin_status_enum.value

        status_colors = {
            "LIVE": "#10b981",       # Emerald
            "RECOVERED": "#06b6d4",  # Cyan
            "DELAYED": "#f59e0b",    # Amber
            "STALE": "#f97316",      # Orange
            "PREDICTED": "#38bdf8",  # Sky Blue
        }
        status_color = status_colors.get(twin_status, "#94a3b8")

        fused_pos_ecef = ekf_snapshot.position_km
        fused_vel_ecef = ekf_snapshot.velocity_km_s
        fused_lat, fused_lon, fused_alt = self.coords.ecef_to_geodetic(fused_pos_ecef)
        vel_mag = float(np.linalg.norm(fused_vel_ecef))

        # 6. Naive Tracker & Delayed Echo Simulation
        if self.faults.feed_cut:
            self._naive_status = "SIGNAL_LOST_FROZEN"
        elif self.faults.sensor_noise_km > 0:
            self._naive_lat = fused_lat + random.uniform(-0.85, 0.85)
            self._naive_lon = fused_lon + random.uniform(-0.85, 0.85)
            self._naive_alt = fused_alt + random.uniform(-60.0, 60.0)
            self._naive_status = "ERRATIC_CORRUPTED"
        else:
            self._naive_lat = fused_lat
            self._naive_lon = fused_lon
            self._naive_alt = fused_alt
            self._naive_status = "TRACKING"

        naive_pos_ecef = self.coords.geodetic_to_ecef(self._naive_lat, self._naive_lon, self._naive_alt) if hasattr(self.coords, "geodetic_to_ecef") else None
        if naive_pos_ecef is not None:
            drift_km = float(np.linalg.norm(fused_pos_ecef - naive_pos_ecef))
        else:
            # Approximate great-circle surface + alt distance
            dlat = math.radians(fused_lat - self._naive_lat)
            dlon = math.radians(fused_lon - self._naive_lon)
            a_hav = math.sin(dlat / 2) ** 2 + math.cos(math.radians(self._naive_lat)) * math.cos(math.radians(fused_lat)) * math.sin(dlon / 2) ** 2
            c_hav = 2 * math.atan2(math.sqrt(a_hav), math.sqrt(max(0.0, 1.0 - a_hav)))
            drift_km = (6371.0 + fused_alt) * c_hav

        delayed_pos_info: dict[str, Any] | None = None
        if self.faults.packet_latency_s > 0.0 and not self.faults.feed_cut:
            t_delayed = now - timedelta(seconds=self.faults.packet_latency_s)
            d_st = self.propagator.propagate(t_delayed)
            d_ecef, _ = self.coords.teme_to_ecef(d_st["position_km"], d_st["velocity_km_s"], t_delayed)
            d_lat, d_lon, d_alt = self.coords.ecef_to_geodetic(d_ecef)
            delayed_pos_info = {
                "latitude": round(d_lat, 4),
                "longitude": round(d_lon, 4),
                "altitude_km": round(d_alt, 2),
                "latency_s": round(self.faults.packet_latency_s, 1),
                "lag_distance_km": round(float(np.linalg.norm(fused_pos_ecef - d_ecef)), 2),
            }

        # 7. Eclipse and Solar Vector Geometry
        eclipse_state: EclipseState = self.eclipse_model.calculate_illumination(
            satellite_pos_km=fused_pos_ecef,
            timestamp=now,
        )

        # 8. Subsystem Health Physics Simulation (EPS & TCS)
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

        self.anomaly_detector.check_subsystem_residuals(
            timestamp=now,
            observed_telemetry={"solar_power_kw": subsystem_summary.power.solar_generation_kw, "core_temp_c": subsystem_summary.thermal.core_temp_c},
            modeled_telemetry={"solar_power_kw": subsystem_summary.power.solar_generation_kw, "core_temp_c": subsystem_summary.thermal.core_temp_c},
        )

        active_anom_list = self.anomaly_detector.get_recent_events(limit=5)
        time_since_meas = (
            (now - self.state_machine.last_measurement_time).total_seconds()
            if self.state_machine.last_measurement_time
            else 0.0
        )

        effective_comms_ms = subsystem_summary.comms_latency_ms + (self.faults.packet_latency_s * 1000.0)
        orbit_track_pts = self._compute_orbit_track(now) if include_orbit_track else []

        return TwinTelemetryFrame(
            timestamp=now.isoformat(),
            satellite_name=self.satellite_name,
            norad_id=self.norad_id,
            twin_status=twin_status,
            status_color=status_color,
            time_since_last_measurement_s=round(time_since_meas, 1),
            inclination_deg=self.spec.inclination_deg,
            orbital_period_min=self.spec.orbital_period_min,
            satellite_description=self.spec.description,
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
                "drift_km": round(drift_km, 2),
            },
            delayed_position=delayed_pos_info,
            rejected_spike=rejected_spike_info,
            orbit_track=orbit_track_pts,
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
                "latency_ms": round(effective_comms_ms, 1),
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
