"""Unit tests for the Extended Kalman Filter (EKF) and uncertainty modeling."""

from datetime import UTC, datetime, timedelta

import numpy as np

from app.twin.ekf import OrbitEKF


def test_ekf_prediction_widens_covariance():
    """Verify that propagating without measurements widens covariance P."""
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    # ISS nominal ECEF state
    pos = np.array([3800.0, -4200.0, 3500.0])
    vel = np.array([4.2, 5.1, -3.2])

    ekf = OrbitEKF(pos, vel, t0)
    initial_sigma = ekf.get_current_state().sigma_pos_km
    initial_conf = ekf.get_current_state().confidence_pct

    # Step forward 30 seconds without measurement
    t1 = t0 + timedelta(seconds=30)
    state_after = ekf.predict(t1)

    assert state_after.sigma_pos_km > initial_sigma
    assert state_after.confidence_pct <= initial_conf
    assert state_after.is_propagated_only is True


def test_ekf_update_contracts_covariance():
    """Verify that fusing an incoming measurement collapses covariance and restores confidence."""
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    pos = np.array([3800.0, -4200.0, 3500.0])
    vel = np.array([4.2, 5.1, -3.2])

    ekf = OrbitEKF(pos, vel, t0)

    # Let it drift for 60 seconds
    t1 = t0 + timedelta(seconds=60)
    drifted_state = ekf.predict(t1)
    drifted_sigma = drifted_state.sigma_pos_km

    # Now fuse fresh measurement at t1
    meas_pos = drifted_state.position_km + np.array([0.05, -0.03, 0.02])
    updated_state, innovation = ekf.update(meas_pos, measurement_timestamp=t1)

    assert updated_state.sigma_pos_km < drifted_sigma
    assert updated_state.is_propagated_only is False
    assert innovation < 0.2  # Small innovation


def test_ekf_rk4_energy_conservation():
    """Verify orbital radius stays within physical LEO bounds over an integration arc."""
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    r_mag = 6778.0  # ~400 km altitude
    v_mag = np.sqrt(398600.4418 / r_mag)  # Circular velocity ~7.66 km/s

    pos = np.array([r_mag, 0.0, 0.0])
    vel = np.array([0.0, v_mag, 0.0])

    ekf = OrbitEKF(pos, vel, t0)
    t_end = t0 + timedelta(minutes=15)  # 15 minutes of orbit
    state = ekf.predict(t_end)

    r_final = np.linalg.norm(state.position_km)
    # Radius shouldn't diverge by more than 2% in a short 15m unperturbed arc
    assert abs(r_final - r_mag) / r_mag < 0.02
