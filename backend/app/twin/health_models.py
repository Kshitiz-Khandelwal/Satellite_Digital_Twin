"""Satellite Subsystem Health Models (EPS, TCS, ADCS, COMMS).

Implements physics-based health simulation for the Digital Twin:
1. Electrical Power Subsystem (EPS):
   - Solar array generation: P_gen = P_max * illumination * cos(theta_incidence)
   - Battery state-of-charge (SoC %): Charges in sunlight, drains in eclipse.
   - Main bus voltage (28V or 120V system).
2. Thermal Control Subsystem (TCS):
   - First-order radiative heat balance:
     m * c_p * dT/dt = Q_solar + Q_albedo + Q_earth_ir + Q_internal - Q_radiated
   - Equilibrium temperature T_eq in Celsius.
3. ADCS & Comms:
   - Nadir pointing error and tracking stability.
   - RF link margin, packet latency, and data health score.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np

from app.twin.eclipse import EclipseState

# Physical Constants
SOLAR_CONSTANT = 1361.0       # W/m^2 (AM0 solar irradiance in LEO)
STEFAN_BOLTZMANN = 5.670374e-8# W/(m^2 K^4)
EARTH_ALBEDO = 0.30           # Average Earth reflectivity
EARTH_IR_FLUX = 237.0         # W/m^2 (Earth terrestrial thermal radiation)


@dataclass
class PowerHealthState:
    solar_generation_kw: float
    battery_soc_pct: float
    bus_voltage_v: float
    net_power_kw: float
    battery_charge_state: str  # "CHARGING", "DISCHARGING", "FLOAT"


@dataclass
class ThermalHealthState:
    core_temp_c: float
    solar_array_temp_c: float
    radiator_flux_w_m2: float
    thermal_balance_status: str # "NOMINAL", "ECLIPSE_COLD", "SUN_WARM"


@dataclass
class SubsystemHealthSummary:
    timestamp: datetime
    power: PowerHealthState
    thermal: ThermalHealthState
    eclipse: EclipseState
    attitude_stability_score: float # 0.0 to 1.0 (1.0 = perfectly locked)
    comms_latency_ms: float
    overall_health_score: float     # 0.0 to 100.0%
    subsystem_flags: list[str]


class SubsystemHealthSimulator:
    """Simulates physical internal states of satellite subsystems."""

    def __init__(
        self,
        nominal_bus_voltage_v: float = 120.0,    # 120V regulated for ISS (or 28V for smallsats)
        max_solar_power_kw: float = 120.0,       # 120 kW peak for ISS arrays
        nominal_base_load_kw: float = 65.0,      # 65 kW nominal load
        battery_capacity_kwh: float = 180.0,     # Battery bank capacity
        spacecraft_thermal_mass_j_k: float = 1.2e6,# Heat capacity
        radiator_area_m2: float = 240.0,
        emissivity: float = 0.85,
        absorptivity: float = 0.25,
    ):
        self.bus_v = nominal_bus_voltage_v
        self.max_solar_kw = max_solar_power_kw
        self.base_load_kw = nominal_base_load_kw
        self.battery_capacity_kwh = battery_capacity_kwh
        self.thermal_mass = spacecraft_thermal_mass_j_k
        self.rad_area = radiator_area_m2
        self.epsilon = emissivity
        self.alpha = absorptivity

        # State variables
        self.battery_soc_pct = 92.5
        self.core_temp_k = 294.15  # 21.0 C
        self.array_temp_k = 310.15 # 37.0 C
        self.last_update_ts: datetime | None = None

    def step(
        self,
        timestamp: datetime,
        position_ecef_km: np.ndarray,
        velocity_ecef_km_s: np.ndarray,
        eclipse_state: EclipseState,
        dt_seconds: float = 1.0
    ) -> SubsystemHealthSummary:
        """Propagates EPS, TCS, ADCS, and COMMS forward by dt_seconds."""
        dt = max(0.1, min(10.0, dt_seconds))
        eta = eclipse_state.illumination_fraction

        # -------------------------------------------------------------
        # 1. Electrical Power Subsystem (EPS)
        # -------------------------------------------------------------
        # Sun-pointing incidence angle variation with orbit position
        r_sat = np.linalg.norm(position_ecef_km)
        pos_unit = position_ecef_km / (r_sat + 1e-9)
        sun_dir = eclipse_state.sun_vector_norm
        cos_theta = max(0.0, float(np.dot(pos_unit, sun_dir)))

        # Solar generation (accounting for illumination and array tracking efficiency)
        generation_kw = self.max_solar_kw * eta * (0.80 + 0.20 * cos_theta)
        # Random small realistic jitter in power consumption
        load_kw = self.base_load_kw + np.sin(timestamp.second / 10.0) * 1.5
        net_power_kw = generation_kw - load_kw

        # Update Battery SoC
        if net_power_kw >= 0:
            # Charging towards 100%
            charging_eff = 0.90
            delta_kwh = net_power_kw * (dt / 3600.0) * charging_eff
            self.battery_soc_pct = min(100.0, self.battery_soc_pct + (delta_kwh / self.battery_capacity_kwh) * 100.0)
            charge_mode = "FLOAT" if self.battery_soc_pct >= 99.5 else "CHARGING"
            voltage = self.bus_v + (0.5 if charge_mode == "CHARGING" else 0.0)
        else:
            # Discharging in shadow / heavy load
            discharge_eff = 0.95
            delta_kwh = abs(net_power_kw) * (dt / 3600.0) / discharge_eff
            self.battery_soc_pct = max(10.0, self.battery_soc_pct - (delta_kwh / self.battery_capacity_kwh) * 100.0)
            charge_mode = "DISCHARGING"
            # Voltage drops slightly under heavy battery discharge
            voltage = self.bus_v - 1.2 * (1.0 - (self.battery_soc_pct / 100.0))

        # -------------------------------------------------------------
        # 2. Thermal Control Subsystem (TCS)
        # -------------------------------------------------------------
        # Inflow: Solar absorption + Earth Albedo + Earth IR + Internal dissipation
        q_solar = self.alpha * SOLAR_CONSTANT * eta
        q_albedo = self.alpha * SOLAR_CONSTANT * EARTH_ALBEDO * max(0.0, cos_theta) * 0.5 * eta
        q_earth_ir = self.epsilon * EARTH_IR_FLUX * 0.45
        q_internal = (load_kw * 1000.0) / max(1.0, self.rad_area)

        q_in_total = (q_solar + q_albedo + q_earth_ir + q_internal) * self.rad_area
        # Outflow: Blackbody radiative emission to deep space (3K background)
        q_out_total = self.epsilon * STEFAN_BOLTZMANN * (self.core_temp_k**4) * self.rad_area

        # Numerical step for core temperature
        dT = ((q_in_total - q_out_total) / self.thermal_mass) * dt
        self.core_temp_k = float(np.clip(self.core_temp_k + dT, 260.0, 315.0))
        core_c = self.core_temp_k - 273.15

        # Solar array external temperature swings faster (low thermal mass)
        if eta > 0.5:
            target_array_k = 338.15 # +65 C in full sun
        else:
            target_array_k = 188.15 # -85 C in deep shadow
        self.array_temp_k += (target_array_k - self.array_temp_k) * (dt / 120.0)
        array_c = self.array_temp_k - 273.15

        if eta < 0.1:
            thermal_status = "ECLIPSE_COLD"
        elif eta > 0.9:
            thermal_status = "SUN_WARM"
        else:
            thermal_status = "NOMINAL"

        # -------------------------------------------------------------
        # 3. ADCS & Comms
        # -------------------------------------------------------------
        # Pointing stability (99.2% nominal, slight jitter)
        pointing_stability = float(np.clip(0.992 + 0.005 * np.cos(timestamp.second / 4.0), 0.95, 1.0))
        # Telemetry transmission latency (ground station pass simulation: 80-140ms)
        comms_latency = round(95.0 + 15.0 * np.sin(timestamp.second / 7.0), 1)

        # Subsystem alerts/flags
        flags = []
        if self.battery_soc_pct < 30.0:
            flags.append("EPS_BATTERY_LOW")
        if core_c < 12.0:
            flags.append("TCS_CORE_COLD_WARNING")
        elif core_c > 35.0:
            flags.append("TCS_CORE_OVERHEAT_WARNING")
        if eta == 0.0:
            flags.append("ORBIT_IN_UMBRA")

        # Composite overall health score
        health_score = 100.0
        if self.battery_soc_pct < 80.0:
            health_score -= (80.0 - self.battery_soc_pct) * 0.5
        if abs(core_c - 21.0) > 8.0:
            health_score -= abs(core_c - 21.0) * 0.8
        health_score = float(np.clip(health_score, 15.0, 100.0))

        return SubsystemHealthSummary(
            timestamp=timestamp,
            power=PowerHealthState(
                solar_generation_kw=round(generation_kw, 2),
                battery_soc_pct=round(self.battery_soc_pct, 1),
                bus_voltage_v=round(voltage, 1),
                net_power_kw=round(net_power_kw, 2),
                battery_charge_state=charge_mode
            ),
            thermal=ThermalHealthState(
                core_temp_c=round(core_c, 1),
                solar_array_temp_c=round(array_c, 1),
                radiator_flux_w_m2=round(q_out_total / self.rad_area, 1),
                thermal_balance_status=thermal_status
            ),
            eclipse=eclipse_state,
            attitude_stability_score=round(pointing_stability, 3),
            comms_latency_ms=comms_latency,
            overall_health_score=round(health_score, 1),
            subsystem_flags=flags
        )
