
from datetime import UTC, datetime

import numpy as np
from sgp4.api import Satrec, jday


class OrbitPropagator:

    def __init__(self, satellite: Satrec):
        self.satellite = satellite

    def propagate(self, timestamp: datetime) -> dict:

        if timestamp.tzinfo is None:
            raise ValueError("Timestamp must be timezone-aware")

        timestamp = timestamp.astimezone(UTC)

        jd, fr = jday(
            timestamp.year,
            timestamp.month,
            timestamp.day,
            timestamp.hour,
            timestamp.minute,
            timestamp.second
            + timestamp.microsecond / 1_000_000,
        )

        error, position, velocity = self.satellite.sgp4(jd, fr)

        if error != 0:
            raise RuntimeError(
                f"SGP4 propagation failed: error code {error}"
            )

        position = np.asarray(position, dtype=float)
        velocity = np.asarray(velocity, dtype=float)

        if not np.all(np.isfinite(position)):
            raise ValueError("Invalid propagated position")

        if not np.all(np.isfinite(velocity)):
            raise ValueError("Invalid propagated velocity")

        return {
            "timestamp": timestamp,
            "position_km": position,
            "velocity_km_s": velocity,
            "coordinate_frame": "TEME",
            "source": "SGP4",
            "status": "PREDICTED",
        }
