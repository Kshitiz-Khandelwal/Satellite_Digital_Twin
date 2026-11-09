"""Eclipse and Solar Illumination Geometry for Satellite Digital Twin.

Computes the position of the Sun in ECEF/TEME, determines Earth shadow geometry,
and calculates the solar illumination fraction:
    - SUNLIGHT: Illumination = 1.0 (Direct line-of-sight to Sun)
    - PENUMBRA: Illumination in (0.0, 1.0) (Partial Earth occultation)
    - UMBRA:    Illumination = 0.0 (Total Earth shadow)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np

# Constants
R_EARTH_KM = 6378.137
R_SUN_KM = 696340.0
AU_KM = 149597870.7


@dataclass
class EclipseState:
    status: str                  # "SUNLIGHT", "PENUMBRA", "UMBRA"
    illumination_fraction: float # 0.0 to 1.0
    sun_vector_norm: np.ndarray  # Unit vector pointing towards Sun
    beta_angle_deg: float        # Orbit plane angle relative to Sun vector
    shadow_depth_km: float       # Penetration depth inside cylinder/cone


class EclipseModel:
    """Calculates Earth-Sun-Satellite occultation geometry."""

    @staticmethod
    def get_sun_position_ecef(timestamp: datetime) -> np.ndarray:
        """Approximates the geocentric position of the Sun in ECEF frame (km).

        Uses standard low-precision solar coordinates (accurate to ~0.01 deg).
        """
        dt_utc = timestamp.astimezone(UTC)
        
        # Julian centuries since J2000.0
        y = dt_utc.year
        m = dt_utc.month
        d = dt_utc.day + (dt_utc.hour + dt_utc.minute / 60.0 + dt_utc.second / 3600.0) / 24.0
        
        if m <= 2:
            y -= 1
            m += 12
        A = int(y / 100)
        B = 2 - A + int(A / 4)
        jd = int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + d + B - 1524.5
        t_cent = (jd - 2451545.0) / 36525.0

        # Mean longitude of the Sun (deg)
        l0 = (280.46646 + 36000.76983 * t_cent) % 360.0
        # Mean anomaly (deg)
        m_deg = (357.52911 + 35999.05029 * t_cent) % 360.0
        m_rad = math.radians(m_deg)

        # Sun ecliptic longitude (deg)
        c = (1.914602 - 0.004817 * t_cent) * math.sin(m_rad) + (0.019993 - 0.000101 * t_cent) * math.sin(2 * m_rad)
        sun_lon_rad = math.radians(l0 + c)

        # Mean obliquity of ecliptic (deg)
        eps = math.radians(23.439291 - 0.0130042 * t_cent)

        # Sun unit vector in ECI
        x_eci = math.cos(sun_lon_rad)
        y_eci = math.sin(sun_lon_rad) * math.cos(eps)
        z_eci = math.sin(sun_lon_rad) * math.sin(eps)

        # Greenwich Mean Sidereal Time (GMST) for ECI -> ECEF rotation
        gmst_deg = (280.46061837 + 360.98564736629 * (jd - 2451545.0)) % 360.0
        theta_rad = math.radians(gmst_deg)

        # Rotate by -GMST around Z axis
        x_ecef = math.cos(theta_rad) * x_eci + math.sin(theta_rad) * y_eci
        y_ecef = -math.sin(theta_rad) * x_eci + math.cos(theta_rad) * y_eci
        z_ecef = z_eci

        sun_vec = np.array([x_ecef, y_ecef, z_ecef], dtype=float)
        return (sun_vec / np.linalg.norm(sun_vec)) * AU_KM

    @classmethod
    def calculate_illumination(
        cls,
        satellite_pos_km: np.ndarray,
        timestamp: datetime,
        orbit_normal: np.ndarray | None = None
    ) -> EclipseState:
        """Determines whether satellite is in direct sunlight or Earth shadow."""
        sat_pos = np.asarray(satellite_pos_km, dtype=float)
        r_sat = float(np.linalg.norm(sat_pos))
        
        sun_pos = cls.get_sun_position_ecef(timestamp)
        sun_dist = float(np.linalg.norm(sun_pos))
        sun_dir = sun_pos / sun_dist

        # Vector from satellite to sun
        d_vec = sun_pos - sat_pos
        u_sun = d_vec / np.linalg.norm(d_vec)

        # Projection along satellite-sun line to find closest approach of Earth center
        # Earth center is at origin (0,0,0)
        # Position of closest point along ray from sat to sun: s = -sat_pos . u_sun
        s = -float(np.dot(sat_pos, u_sun))

        # Beta angle calculation (angle between orbit plane normal and sun vector)
        beta_deg = 0.0
        if orbit_normal is not None:
            norm_unit = orbit_normal / (np.linalg.norm(orbit_normal) + 1e-9)
            sin_beta = float(np.clip(np.dot(norm_unit, sun_dir), -1.0, 1.0))
            beta_deg = math.degrees(math.asin(sin_beta))

        if s <= 0:
            # Satellite is between Earth and Sun -> fully illuminated
            return EclipseState(
                status="SUNLIGHT",
                illumination_fraction=1.0,
                sun_vector_norm=sun_dir,
                beta_angle_deg=round(beta_deg, 2),
                shadow_depth_km=0.0
            )

        # Perpendicular distance from Earth center to line-of-sight
        d_perp = math.sqrt(max(0.0, r_sat**2 - s**2))

        # Conical shadow boundaries at distance s behind Earth
        tan_alpha_umbra = (R_SUN_KM - R_EARTH_KM) / AU_KM
        tan_alpha_penumbra = (R_SUN_KM + R_EARTH_KM) / AU_KM

        r_umbra = max(0.0, R_EARTH_KM - s * tan_alpha_umbra)
        r_penumbra = R_EARTH_KM + s * tan_alpha_penumbra

        if d_perp <= r_umbra:
            # Inside total shadow
            return EclipseState(
                status="UMBRA",
                illumination_fraction=0.0,
                sun_vector_norm=sun_dir,
                beta_angle_deg=round(beta_deg, 2),
                shadow_depth_km=round(r_umbra - d_perp, 2)
            )
        elif d_perp < r_penumbra:
            # Partial occultation
            fraction = float(np.clip((d_perp - r_umbra) / max(1.0, r_penumbra - r_umbra), 0.05, 0.95))
            return EclipseState(
                status="PENUMBRA",
                illumination_fraction=round(fraction, 3),
                sun_vector_norm=sun_dir,
                beta_angle_deg=round(beta_deg, 2),
                shadow_depth_km=0.0
            )
        else:
            return EclipseState(
                status="SUNLIGHT",
                illumination_fraction=1.0,
                sun_vector_norm=sun_dir,
                beta_angle_deg=round(beta_deg, 2),
                shadow_depth_km=0.0
            )
