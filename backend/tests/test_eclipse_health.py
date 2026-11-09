"""Unit tests for Eclipse geometry and Subsystem Health simulation."""

from datetime import UTC, datetime

import numpy as np

from app.twin.eclipse import EclipseModel
from app.twin.health_models import SubsystemHealthSimulator


def test_eclipse_sunlight_vs_umbra():
    """Verify eclipse model identifies sunlight vs Earth shadow correctly."""
    now = datetime(2026, 3, 20, 12, 0, 0, tzinfo=UTC)  # Vernal equinox (Sun on +X/Y)
    sun_pos = EclipseModel.get_sun_position_ecef(now)
    sun_dir = sun_pos / np.linalg.norm(sun_pos)

    # Position 1: Satellite directly on day side facing the Sun
    pos_day = sun_dir * 6800.0
    state_day = EclipseModel.calculate_illumination(pos_day, now)
    assert state_day.status == "SUNLIGHT"
    assert state_day.illumination_fraction == 1.0

    # Position 2: Satellite directly on night side behind Earth
    pos_night = -sun_dir * 6800.0
    state_night = EclipseModel.calculate_illumination(pos_night, now)
    assert state_night.status == "UMBRA"
    assert state_night.illumination_fraction == 0.0


def test_health_simulator_power_in_sunlight_and_eclipse():
    """Verify solar power generates in sunlight and battery drains in eclipse."""
    sim = SubsystemHealthSimulator()
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    pos = np.array([6778.0, 0.0, 0.0])
    vel = np.array([0.0, 7.66, 0.0])

    # Sunlight step
    ecl_sun = EclipseModel.calculate_illumination(pos, now)
    ecl_sun.status = "SUNLIGHT"
    ecl_sun.illumination_fraction = 1.0

    res_sun = sim.step(now, pos, vel, ecl_sun, dt_seconds=1.0)
    assert res_sun.power.solar_generation_kw > 50.0  # Generating significant power
    assert res_sun.power.battery_charge_state in ("CHARGING", "FLOAT")

    # Eclipse step
    ecl_shadow = EclipseModel.calculate_illumination(-pos, now)
    ecl_shadow.status = "UMBRA"
    ecl_shadow.illumination_fraction = 0.0

    res_shadow = sim.step(now, pos, vel, ecl_shadow, dt_seconds=1.0)
    assert res_shadow.power.solar_generation_kw == 0.0  # Zero solar generation in umbra
    assert res_shadow.power.battery_charge_state == "DISCHARGING"
    assert res_shadow.power.net_power_kw < 0.0
