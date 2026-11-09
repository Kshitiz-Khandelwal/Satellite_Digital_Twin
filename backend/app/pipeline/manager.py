"""Unified Satellite Digital Twin Pipeline Manager.

Coordinates all twin subsystems into an authoritative real-time pipeline:
1. SGP4 physics propagation baseline.
2. Ingestion from CelesTrak / Simulator.
3. Fault injection sandbox (for live demos).
4. Data hygiene gate (MAD outlier filter & range checks).
5. Extended Kalman Filter (EKF) state estimation with covariance.
6. Eclipse & solar illumination geometry.
7. Subsystem health models (Power EPS, Thermal TCS, ADCS, Comms).
8. Residual anomaly detection.
9. State machine transitions (LIVE -> DELAYED -> STALE -> PREDICTED -> RECOVERED).
10. Forecast trajectory & ground track generator.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
from sgp4.api import Satrec

from app.adapters.simulator import SimulatorAdapter
from app.models.measurement import Measurement
from app.pipeline.fault_injector import FaultInjector
from app.pipeline.hygiene import DataHygieneGate
from app.pipeline.state_machine import TwinStateMachine
from app.twin.anomaly_detector import ResidualAnomalyDetector
from app.twin.coordinate import CoordinateConverter
from app.twin.eclipse import EclipseModel
from app.twin.ekf import OrbitEKF
from app.twin.health_models import SubsystemHealthSimulator
from app.twin.propagator import OrbitPropagator

logger = logging.getLogger(__name__)


class SatelliteTwinManager:
    """Master orchestrator for a single satellite's digital twin."""

    def __init__(
        self,
        norad_id: int = 25544,
        satellite_name: str = "ISS (ZARYA)",
        use_simulator: bool = True
    ):
        self.norad_id = norad_id
        self.satellite_name = satellite_name
        self.use_simulator = use_simulator

        # Initialize core components
        self.simulator = SimulatorAdapter(satellite_id=norad_id, interval_seconds=1.0)
        self.satrec: Satrec = self.simulator.satellite
        self.propagator = OrbitPropagator(self.satrec)

        now = datetime.now(UTC)
        initial_prop = self.propagator.propagate(now)
        pos_teme = initial_prop["position_km"]
        vel_teme = initial_prop["velocity_km_s"]
        pos_ecef, vel_ecef = CoordinateConverter.teme_to_ecef(pos_teme, vel_teme, now)

        # EKF Filter
        self.ekf = OrbitEKF(
            initial_position_km=pos_ecef,
            initial_velocity_km_s=vel_ecef,
            initial_timestamp=now
        )

        # Pipeline modules
        self.hygiene = DataHygieneGate()
        self.health_sim = SubsystemHealthSimulator()
        self.anomaly_detector = ResidualAnomalyDetector()
        self.state_machine = TwinStateMachine()
        self.fault_injector = FaultInjector()

        self.last_step_time = now
        self.cached_forecast: list[dict[str, Any]] = []
        self.last_forecast_time: datetime | None = None
        self.step_counter = 0

    def set_fault_injection(self, **kwargs) -> dict[str, Any]:
        """Exposes dynamic fault injection to frontend control panel."""
        return self.fault_injector.update_config(**kwargs)

    def get_fault_config(self) -> dict[str, Any]:
        return self.fault_injector.get_config_dict()

    async def step(self, current_time: datetime | None = None) -> dict[str, Any]:
        """Executes one 1 Hz iteration of the Digital Twin cycle."""
        now = (current_time or datetime.now(UTC)).astimezone(UTC)
        dt = (now - self.last_step_time).total_seconds()
        self.last_step_time = now
        self.step_counter += 1

        # -------------------------------------------------------------
        # 1. Physics Baseline Propagation (SGP4)
        # -------------------------------------------------------------
        try:
            sgp4_res = self.propagator.propagate(now)
            pos_teme = sgp4_res["position_km"]
            vel_teme = sgp4_res["velocity_km_s"]
            pos_ecef_sgp4, vel_ecef_sgp4 = CoordinateConverter.teme_to_ecef(pos_teme, vel_teme, now)
        except Exception as e:
            logger.warning(f"SGP4 propagation step warning: {e}")
            pos_ecef_sgp4 = self.ekf.state[:3]
            vel_ecef_sgp4 = self.ekf.state[3:]

        # -------------------------------------------------------------
        # 2. Raw Telemetry Generation / Ingestion
        # -------------------------------------------------------------
        raw_meas: Measurement | None = None
        if self.use_simulator:
            raw_meas = await self.simulator.generate_measurement(now)
            raw_pos_ecef = raw_meas.fields["position_km"]
            raw_vel_ecef = raw_meas.fields.get("velocity_km_s")
        else:
            raw_pos_ecef = pos_ecef_sgp4
            raw_vel_ecef = vel_ecef_sgp4

        # -------------------------------------------------------------
        # 3. Fault Injection Sandbox
        # -------------------------------------------------------------
        (
            packet_delivered,
            faulty_pos,
            faulty_vel,
            simulated_latency,
            applied_faults
        ) = self.fault_injector.process_incoming_packet(
            raw_position_km=raw_pos_ecef,
            raw_velocity_km_s=raw_vel_ecef,
            timestamp=now
        )

        # -------------------------------------------------------------
        # 4. Data Hygiene Gate (Outlier / MAD / Dedupe / Range)
        # -------------------------------------------------------------
        accepted_by_hygiene = False
        rejection_reasons = []
        if packet_delivered and faulty_pos is not None:
            synth_meas = Measurement(
                satellite_id=self.norad_id,
                source="SIMULATOR" if self.use_simulator else "CELESTRAK",
                event_time=now - timedelta(seconds=simulated_latency),
                receive_time=now,
                fields={
                    "position_km": faulty_pos.tolist(),
                    "velocity_km_s": faulty_vel.tolist() if faulty_vel is not None else None,
                }
            )
            accepted_by_hygiene, sanitized_data, rejection_reasons = self.hygiene.process(synth_meas)

        # -------------------------------------------------------------
        # 5. EKF Fusion & Uncertainty Estimation
        # -------------------------------------------------------------
        if accepted_by_hygiene and sanitized_data is not None:
            # Fuse measurement into Kalman filter
            ekf_state, innovation_km = self.ekf.update(
                measured_position_km=sanitized_data["position_km"],
                measured_velocity_km_s=sanitized_data["velocity_km_s"],
                measurement_timestamp=now
            )
            # Evaluate statistical innovation for anomaly alerts
            self.anomaly_detector.check_position_innovation(now, innovation_km)
            fresh_packet_fused = True
        else:
            # Coasting with orbital dynamics: covariance expands!
            ekf_state = self.ekf.predict(now)
            fresh_packet_fused = False

        # -------------------------------------------------------------
        # 6. Geodetic Coordinate Conversion
        # -------------------------------------------------------------
        lat, lon, alt = CoordinateConverter.ecef_to_geodetic(ekf_state.position_km)
        speed_km_s = float(np.linalg.norm(ekf_state.velocity_km_s))

        # -------------------------------------------------------------
        # 7. Eclipse Geometry & Subsystem Health Models
        # -------------------------------------------------------------
        orbit_norm = np.cross(ekf_state.position_km, ekf_state.velocity_km_s)
        eclipse_state = EclipseModel.calculate_illumination(ekf_state.position_km, now, orbit_norm)
        health_summary = self.health_sim.step(
            timestamp=now,
            position_ecef_km=ekf_state.position_km,
            velocity_ecef_km_s=ekf_state.velocity_km_s,
            eclipse_state=eclipse_state,
            dt_seconds=dt
        )

        # Check subsystem health residuals
        observed_subsys = {
            "solar_power_kw": health_summary.power.solar_generation_kw,
            "core_temp_c": health_summary.thermal.core_temp_c
        }
        self.anomaly_detector.check_subsystem_residuals(now, observed_subsys, observed_subsys)

        # -------------------------------------------------------------
        # 8. Twin State Machine Transition
        # -------------------------------------------------------------
        self.state_machine.update(
            current_time=now,
            fresh_packet_arrived=fresh_packet_fused,
            packet_latency_seconds=simulated_latency,
            confidence_pct=ekf_state.confidence_pct
        )
        status_info = self.state_machine.get_status_info(now, ekf_state.confidence_pct)

        # -------------------------------------------------------------
        # 9. Forecast Trajectory & Ground Track (60 minutes)
        # -------------------------------------------------------------
        if self.last_forecast_time is None or (now - self.last_forecast_time).total_seconds() > 60.0:
            self.cached_forecast = self._generate_forecast(now, duration_minutes=60, step_minutes=2)
            self.last_forecast_time = now

        # -------------------------------------------------------------
        # 10. Naive Tracker Baseline (for side-by-side comparison)
        # -------------------------------------------------------------
        naive_pos = self.fault_injector.naive_position_ecef_km
        naive_geodetic = None
        if naive_pos is not None:
            n_lat, n_lon, n_alt = CoordinateConverter.ecef_to_geodetic(naive_pos)
            naive_geodetic = {
                "latitude": round(n_lat, 4),
                "longitude": round(n_lon, 4),
                "altitude_km": round(n_alt, 2),
                "status": self.fault_injector.naive_status
            }

        # Pack unified telemetry payload
        return {
            "satellite": {
                "norad_id": self.norad_id,
                "name": self.satellite_name,
                "timestamp": now.isoformat(),
            },
            "twin_state": status_info,
            "telemetry": {
                "position_ecef_km": [round(float(v), 3) for v in ekf_state.position_km],
                "velocity_ecef_km_s": [round(float(v), 4) for v in ekf_state.velocity_km_s],
                "latitude": round(lat, 4),
                "longitude": round(lon, 4),
                "altitude_km": round(alt, 2),
                "velocity_magnitude_km_s": round(speed_km_s, 4),
            },
            "fusion": {
                "confidence_pct": ekf_state.confidence_pct,
                "sigma_pos_km": ekf_state.sigma_pos_km,
                "confidence_zone_radius_km": round(ekf_state.sigma_pos_km * 3.0, 2), # 3-sigma bounds
                "is_propagated_only": ekf_state.is_propagated_only,
                "innovation_residual_km": ekf_state.innovation_residual_km,
            },
            "subsystems": {
                "power": {
                    "solar_generation_kw": health_summary.power.solar_generation_kw,
                    "battery_soc_pct": health_summary.power.battery_soc_pct,
                    "bus_voltage_v": health_summary.power.bus_voltage_v,
                    "net_power_kw": health_summary.power.net_power_kw,
                    "charge_mode": health_summary.power.battery_charge_state,
                },
                "thermal": {
                    "core_temp_c": health_summary.thermal.core_temp_c,
                    "solar_array_temp_c": health_summary.thermal.solar_array_temp_c,
                    "status": health_summary.thermal.thermal_balance_status,
                },
                "eclipse": {
                    "status": eclipse_state.status,
                    "illumination_fraction": eclipse_state.illumination_fraction,
                    "beta_angle_deg": eclipse_state.beta_angle_deg,
                },
                "comms": {
                    "pointing_stability": health_summary.attitude_stability_score,
                    "latency_ms": health_summary.comms_latency_ms,
                    "overall_health_score": health_summary.overall_health_score,
                    "active_flags": health_summary.subsystem_flags,
                }
            },
            "anomalies": self.anomaly_detector.get_recent_events(limit=5),
            "fault_injection": {
                "active_config": self.fault_injector.get_config_dict(),
                "applied_faults_this_frame": applied_faults,
                "rejection_reasons": rejection_reasons,
                "naive_tracker": naive_geodetic
            },
            "forecast_track": self.cached_forecast,
        }

    def _generate_forecast(
        self,
        start_time: datetime,
        duration_minutes: int = 60,
        step_minutes: int = 2
    ) -> list[dict[str, Any]]:
        """Generates future orbital ground track and positions for 3D Cesium projection."""
        points = []
        steps = int(duration_minutes / step_minutes)
        for i in range(steps + 1):
            t = start_time + timedelta(minutes=i * step_minutes)
            try:
                prop = self.propagator.propagate(t)
                pos_ecef, _ = CoordinateConverter.teme_to_ecef(
                    prop["position_km"], prop["velocity_km_s"], t
                )
                lat, lon, alt = CoordinateConverter.ecef_to_geodetic(pos_ecef)
                points.append({
                    "time": t.isoformat(),
                    "latitude": round(lat, 4),
                    "longitude": round(lon, 4),
                    "altitude_km": round(alt, 2),
                    "position_ecef": [round(float(v), 2) for v in pos_ecef]
                })
            except Exception:
                continue
        return points
