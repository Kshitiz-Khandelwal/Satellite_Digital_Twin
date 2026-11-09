"""Extended Kalman Filter (EKF) for Satellite Orbit Estimation & Fusion.

State vector x in R^6:
    x = [x, y, z, vx, vy, vz]^T  (Position in km, Velocity in km/s)

Continuous dynamics (Two-body gravitational acceleration):
    r = sqrt(x^2 + y^2 + z^2)
    a = -mu / r^3 * [x, y, z]^T

Discrete propagation (dt):
    x_k+1 = f(x_k, dt) using Runge-Kutta 4th order (RK4)
    F = Jacobian df/dx evaluated at x_k
    P_k+1^- = F * P_k * F^T + Q

Measurement update:
    z in R^3 (measured position) or R^6 (position + velocity)
    y = z - H * x^-  (Innovation)
    S = H * P^- * H^T + R  (Innovation covariance)
    K = P^- * H^T * S^-1  (Kalman Gain)
    x^+ = x^- + K * y
    P^+ = (I - K * H) * P^-

Covariance & Confidence:
    sigma_pos = sqrt(Tr(P_pos))  in km (3D position 1-sigma uncertainty)
    confidence_pct = 100 * exp(-sigma_pos / 5.0)  (100% when tight, drops as P widens)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np

# Standard Earth gravitational parameter (km^3 / s^2)
MU_EARTH = 398600.4418
# Earth mean radius (km)
R_EARTH = 6378.137


@dataclass
class EKFState:
    """Represents a state snapshot with uncertainty."""
    timestamp: datetime
    position_km: np.ndarray          # [x, y, z] in km
    velocity_km_s: np.ndarray        # [vx, vy, vz] in km/s
    covariance: np.ndarray           # 6x6 covariance matrix P
    sigma_pos_km: float              # 3D position standard deviation sqrt(trace(P_pos))
    confidence_pct: float            # 0.0 to 100.0%
    is_propagated_only: bool = False # True if no measurement was fused (coasting)
    innovation_residual_km: float = 0.0


class OrbitEKF:
    """Extended Kalman Filter for 6-DOF orbital kinematic estimation."""

    def __init__(
        self,
        initial_position_km: np.ndarray,
        initial_velocity_km_s: np.ndarray,
        initial_timestamp: datetime,
        process_noise_pos: float = 1e-4,    # km^2 / s
        process_noise_vel: float = 1e-6,    # (km/s)^2 / s
        measurement_noise_pos: float = 0.04,# km (approx 200m 1-sigma)
        measurement_noise_vel: float = 0.005# km/s
    ):
        self.state = np.hstack([
            np.asarray(initial_position_km, dtype=float),
            np.asarray(initial_velocity_km_s, dtype=float)
        ])  # Shape (6,)
        
        self.timestamp = initial_timestamp.astimezone(UTC)
        
        # Initial covariance: moderate confidence
        self.P = np.diag([
            0.05**2, 0.05**2, 0.05**2,     # Position variances (km^2)
            0.005**2, 0.005**2, 0.005**2   # Velocity variances (km/s)^2
        ])

        self.q_pos = process_noise_pos
        self.q_vel = process_noise_vel
        self.r_pos = measurement_noise_pos**2
        self.r_vel = measurement_noise_vel**2
        
        self.last_innovation_km = 0.0
        self.is_propagated_only = False

    @staticmethod
    def _orbit_derivatives(state: np.ndarray) -> np.ndarray:
        """Computes [vx, vy, vz, ax, ay, az] under central Earth gravity."""
        pos = state[:3]
        vel = state[3:]
        r = np.linalg.norm(pos)
        if r < 100.0:  # Numerical safety guard
            r = R_EARTH
        acc = -MU_EARTH / (r**3) * pos
        return np.hstack([vel, acc])

    def _rk4_step(self, state: np.ndarray, dt: float) -> np.ndarray:
        """4th-order Runge-Kutta numerical integration for orbital motion."""
        k1 = self._orbit_derivatives(state)
        k2 = self._orbit_derivatives(state + 0.5 * dt * k1)
        k3 = self._orbit_derivatives(state + 0.5 * dt * k2)
        k4 = self._orbit_derivatives(state + dt * k3)
        return state + (dt / 6.0) * (k1 + 2.0 * k2 + 2.0 * k3 + k4)

    def _compute_state_transition_jacobian(self, state: np.ndarray, dt: float) -> np.ndarray:
        """First-order Jacobian F = df/dx for continuous two-body dynamics discretized by dt."""
        pos = state[:3]
        r = np.linalg.norm(pos)
        if r < 100.0:
            r = R_EARTH
        
        # Gravity gradient tensor G = d(acc)/d(pos) = -mu/r^3 * I + 3*mu/r^5 * (r r^T)
        I3 = np.eye(3)
        rrT = np.outer(pos, pos)
        G = (-MU_EARTH / (r**3)) * I3 + (3.0 * MU_EARTH / (r**5)) * rrT
        
        # State transition matrix F (first-order Magnus/Taylor expansion)
        F = np.eye(6)
        F[:3, 3:] = I3 * dt
        F[3:, :3] = G * dt
        return F

    def predict(self, target_timestamp: datetime) -> EKFState:
        """Propagates state and covariance forward to target_timestamp (coasting / forecast)."""
        target_timestamp = target_timestamp.astimezone(UTC)
        dt = (target_timestamp - self.timestamp).total_seconds()
        
        self.is_propagated_only = True
        if dt <= 0:
            self.timestamp = target_timestamp
            return self.get_current_state(is_propagated_only=True)
        
        # Substep integration for accuracy if dt > 2.0 seconds
        substeps = max(1, int(math.ceil(abs(dt) / 1.0)))
        sub_dt = dt / substeps
        
        curr_state = self.state.copy()
        for _ in range(substeps):
            curr_state = self._rk4_step(curr_state, sub_dt)
            F = self._compute_state_transition_jacobian(curr_state, sub_dt)
            
            # Process noise Q grows linearly with dt
            Q = np.diag([
                self.q_pos * sub_dt, self.q_pos * sub_dt, self.q_pos * sub_dt,
                self.q_vel * sub_dt, self.q_vel * sub_dt, self.q_vel * sub_dt
            ])
            self.P = F @ self.P @ F.T + Q
        
        self.state = curr_state
        self.timestamp = target_timestamp
        
        # Symmetrize P to prevent numerical drift
        self.P = 0.5 * (self.P + self.P.T)
        self.is_propagated_only = True
        
        return self.get_current_state(is_propagated_only=True)

    def update(
        self,
        measured_position_km: np.ndarray,
        measured_velocity_km_s: np.ndarray | None = None,
        measurement_timestamp: datetime | None = None
    ) -> tuple[EKFState, float]:
        """Fuses incoming measurement to correct state and collapse covariance."""
        if measurement_timestamp is not None:
            self.predict(measurement_timestamp)
            
        meas_pos = np.asarray(measured_position_km, dtype=float)
        
        if measured_velocity_km_s is not None:
            # Full 6-state observation
            meas_vel = np.asarray(measured_velocity_km_s, dtype=float)
            z = np.hstack([meas_pos, meas_vel])
            H = np.eye(6)
            R = np.diag([self.r_pos] * 3 + [self.r_vel] * 3)
            pred_z = self.state
        else:
            # Position-only observation (3 states)
            z = meas_pos
            H = np.zeros((3, 6))
            H[:3, :3] = np.eye(3)
            R = np.diag([self.r_pos] * 3)
            pred_z = self.state[:3]
            
        # Innovation y
        y = z - pred_z
        innovation_dist_km = float(np.linalg.norm(y[:3]))
        self.last_innovation_km = innovation_dist_km
        
        # Innovation covariance S
        S = H @ self.P @ H.T + R
        
        # Kalman Gain K
        try:
            K = self.P @ H.T @ np.linalg.inv(S)
        except np.linalg.LinAlgError:
            K = self.P @ H.T @ np.linalg.pinv(S)
            
        # State update
        self.state = self.state + K @ y
        
        # Covariance update (Joseph stabilized form: P = (I - KH)P(I - KH)^T + KRK^T)
        I_KH = np.eye(6) - K @ H
        self.P = I_KH @ self.P @ I_KH.T + K @ R @ K.T
        self.P = 0.5 * (self.P + self.P.T)
        self.is_propagated_only = False
        
        state_out = self.get_current_state(is_propagated_only=False)
        return state_out, innovation_dist_km

    def get_current_state(self, is_propagated_only: bool | None = None) -> EKFState:
        """Returns the current EKFState snapshot with computed metrics."""
        if is_propagated_only is None:
            is_propagated_only = self.is_propagated_only
        pos_cov_trace = float(np.trace(self.P[:3, :3]))
        sigma_pos = math.sqrt(max(0.0, pos_cov_trace))
        
        # Confidence decays exponentially as 1-sigma uncertainty widens
        confidence_pct = float(np.clip(100.0 * math.exp(-sigma_pos / 6.0), 5.0, 99.8))
        
        return EKFState(
            timestamp=self.timestamp,
            position_km=self.state[:3].copy(),
            velocity_km_s=self.state[3:].copy(),
            covariance=self.P.copy(),
            sigma_pos_km=round(sigma_pos, 4),
            confidence_pct=round(confidence_pct, 1),
            is_propagated_only=is_propagated_only,
            innovation_residual_km=round(self.last_innovation_km, 4)
        )
