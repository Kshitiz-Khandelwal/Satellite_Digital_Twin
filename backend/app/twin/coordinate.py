
from datetime import datetime, timezone

import numpy as np

from skyfield.sgp4lib import TEME_to_ITRF
from skyfield.constants import AU_KM, DAY_S
from sgp4.api import jday


class CoordinateConverter:

    A = 6378.137
    F = 1 / 298.257223563
    E2 = F * (2 - F)

    @classmethod
    def teme_to_ecef(
        cls,
        position_km: np.ndarray,
        velocity_km_s: np.ndarray,
        timestamp: datetime,
    ) -> tuple[np.ndarray, np.ndarray]:

        if timestamp.tzinfo is None:
            raise ValueError("Timestamp must be timezone-aware")

        timestamp = timestamp.astimezone(timezone.utc)

        position_km = np.asarray(position_km, dtype=float)
        velocity_km_s = np.asarray(velocity_km_s, dtype=float)

        if position_km.shape != (3,) or velocity_km_s.shape != (3,):
            raise ValueError("Position and velocity must have 3 components")

        if not np.all(np.isfinite(position_km)):
            raise ValueError("Invalid position")

        if not np.all(np.isfinite(velocity_km_s)):
            raise ValueError("Invalid velocity")

        jd, fr = jday(
            timestamp.year,
            timestamp.month,
            timestamp.day,
            timestamp.hour,
            timestamp.minute,
            timestamp.second + timestamp.microsecond / 1_000_000,
        )

        position_au = position_km / AU_KM
        velocity_au_day = velocity_km_s * DAY_S / AU_KM

        position_itrf, velocity_itrf = TEME_to_ITRF(
            jd,
            position_au,
            velocity_au_day,
            0.0,
            0.0,
            fr,
        )

        position_ecef = np.asarray(position_itrf) * AU_KM
        velocity_ecef = np.asarray(velocity_itrf) * AU_KM / DAY_S

        return position_ecef, velocity_ecef

    @classmethod
    def ecef_to_geodetic(
        cls,
        position_ecef: np.ndarray,
    ) -> tuple[float, float, float]:

        position_ecef = np.asarray(position_ecef, dtype=float)

        if position_ecef.shape != (3,):
            raise ValueError("ECEF position must have 3 components")

        if not np.all(np.isfinite(position_ecef)):
            raise ValueError("Invalid ECEF position")

        x, y, z = position_ecef
        p = np.hypot(x, y)

        if p < 1e-10:
            if abs(z) < 1e-10:
                raise ValueError("Earth centre has no geodetic coordinates")

            latitude = np.copysign(90.0, z)
            altitude = abs(z) - cls.A * np.sqrt(1 - cls.E2)

            return float(latitude), 0.0, float(altitude)

        longitude = np.arctan2(y, x)
        latitude = np.arctan2(z, p * (1 - cls.E2))

        for _ in range(10):
            sin_lat = np.sin(latitude)
            n = cls.A / np.sqrt(1 - cls.E2 * sin_lat**2)
            altitude = p / np.cos(latitude) - n

            new_latitude = np.arctan2(
                z,
                p * (1 - cls.E2 * n / (n + altitude)),
            )

            if abs(new_latitude - latitude) < 1e-12:
                latitude = new_latitude
                break

            latitude = new_latitude

        sin_lat = np.sin(latitude)
        n = cls.A / np.sqrt(1 - cls.E2 * sin_lat**2)
        altitude = p / np.cos(latitude) - n

        return (
            float(np.degrees(latitude)),
            float(np.degrees(longitude)),
            float(altitude),
        )
