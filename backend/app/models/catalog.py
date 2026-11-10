"""Orbital element catalog for selectable real-world spacecraft targets."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SatelliteSpec:
    norad_id: int
    name: str
    short_code: str
    description: str
    inclination_deg: float
    eccentricity: float
    mean_motion_rev_day: float
    raan_deg: float
    arg_perigee_deg: float
    mean_anomaly_deg: float
    nominal_altitude_km: float
    orbital_period_min: float
    max_solar_power_kw: float
    nominal_base_load_kw: float
    nominal_bus_voltage_v: float
    battery_capacity_kwh: float


SATELLITE_CATALOG: dict[int, SatelliteSpec] = {
    25544: SatelliteSpec(
        norad_id=25544,
        name="ISS (ZARYA)",
        short_code="ISS ZARYA",
        description="International Space Station · Crewed Low Earth Orbit",
        inclination_deg=51.64,
        eccentricity=0.0006,
        mean_motion_rev_day=15.50,
        raan_deg=45.0,
        arg_perigee_deg=30.0,
        mean_anomaly_deg=100.0,
        nominal_altitude_km=418.0,
        orbital_period_min=92.9,
        max_solar_power_kw=120.0,
        nominal_base_load_kw=65.0,
        nominal_bus_voltage_v=120.0,
        battery_capacity_kwh=180.0,
    ),
    20580: SatelliteSpec(
        norad_id=20580,
        name="HST (HUBBLE)",
        short_code="HST HUBBLE",
        description="Hubble Space Telescope · Low-Inclination Observatory Orbit",
        inclination_deg=28.47,
        eccentricity=0.0003,
        mean_motion_rev_day=15.10,
        raan_deg=120.0,
        arg_perigee_deg=65.0,
        mean_anomaly_deg=40.0,
        nominal_altitude_km=538.0,
        orbital_period_min=95.4,
        max_solar_power_kw=5.5,
        nominal_base_load_kw=2.4,
        nominal_bus_voltage_v=28.0,
        battery_capacity_kwh=12.0,
    ),
    43013: SatelliteSpec(
        norad_id=43013,
        name="NOAA-20",
        short_code="NOAA-20",
        description="NOAA-20 Polar Weather Satellite · Sun-Synchronous Polar Orbit",
        inclination_deg=98.74,
        eccentricity=0.0002,
        mean_motion_rev_day=14.20,
        raan_deg=210.0,
        arg_perigee_deg=90.0,
        mean_anomaly_deg=200.0,
        nominal_altitude_km=824.0,
        orbital_period_min=101.4,
        max_solar_power_kw=4.2,
        nominal_base_load_kw=2.1,
        nominal_bus_voltage_v=28.0,
        battery_capacity_kwh=9.5,
    ),
    48274: SatelliteSpec(
        norad_id=48274,
        name="TIANGONG (CSS)",
        short_code="TIANGONG CSS",
        description="Tiangong Space Station · Mid-Inclination Modular LEO",
        inclination_deg=41.47,
        eccentricity=0.0005,
        mean_motion_rev_day=15.60,
        raan_deg=310.0,
        arg_perigee_deg=15.0,
        mean_anomaly_deg=270.0,
        nominal_altitude_km=390.0,
        orbital_period_min=92.3,
        max_solar_power_kw=40.0,
        nominal_base_load_kw=22.0,
        nominal_bus_voltage_v=100.0,
        battery_capacity_kwh=75.0,
    ),
}


def get_satellite_spec(norad_id: int) -> SatelliteSpec:
    """Returns catalog specification for norad_id, falling back safely to ISS or custom LEO."""
    if norad_id in SATELLITE_CATALOG:
        return SATELLITE_CATALOG[norad_id]
    return SatelliteSpec(
        norad_id=norad_id,
        name=f"NORAD-{norad_id}",
        short_code=f"SAT-{norad_id}",
        description=f"Tracked Space Object NORAD {norad_id}",
        inclination_deg=51.64,
        eccentricity=0.0005,
        mean_motion_rev_day=15.50,
        raan_deg=45.0,
        arg_perigee_deg=30.0,
        mean_anomaly_deg=100.0,
        nominal_altitude_km=420.0,
        orbital_period_min=92.9,
        max_solar_power_kw=120.0,
        nominal_base_load_kw=65.0,
        nominal_bus_voltage_v=120.0,
        battery_capacity_kwh=180.0,
    )
