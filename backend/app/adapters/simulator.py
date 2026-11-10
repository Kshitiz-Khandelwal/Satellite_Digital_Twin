import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import numpy as np
from sgp4.api import WGS72, Satrec, jday

from app.models.catalog import get_satellite_spec
from app.models.measurement import Measurement

# Deterministic reference epoch so stateless serverless containers compute continuous motion
REFERENCE_EPOCH = datetime(2026, 11, 1, 0, 0, 0, tzinfo=UTC)


class SimulatorAdapter:

    def __init__(
        self,
        satellite_id: int = 25544,
        interval_seconds: float = 1.0,
        noise_km: float = 0.05,
        seed: int = 42,
    ):
        if interval_seconds <= 0:
            raise ValueError("Interval must be positive")

        if noise_km < 0:
            raise ValueError("Noise cannot be negative")

        self.satellite_id = satellite_id
        self.interval_seconds = interval_seconds
        self.noise_km = noise_km

        self.rng = np.random.default_rng(seed)
        self.spec = get_satellite_spec(satellite_id)
        self.satellite = self._create_orbit()

    def _create_orbit(self) -> Satrec:
        satellite = Satrec()
        ref = REFERENCE_EPOCH

        jd, fr = jday(
            ref.year,
            ref.month,
            ref.day,
            ref.hour,
            ref.minute,
            ref.second + ref.microsecond / 1_000_000,
        )

        epoch = jd + fr - 2433281.5

        satellite.sgp4init(
            WGS72,
            "i",
            self.satellite_id,
            epoch,
            0.0,
            0.0,
            0.0,
            self.spec.eccentricity,
            np.deg2rad(self.spec.arg_perigee_deg),
            np.deg2rad(self.spec.inclination_deg),
            np.deg2rad(self.spec.raan_deg),
            self.spec.mean_motion_rev_day * 2 * np.pi / 1440.0,
            np.deg2rad(self.spec.mean_anomaly_deg),
        )

        return satellite

    def _propagate(self, timestamp: datetime):
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
                f"SGP4 propagation failed with error code {error}"
            )

        return position, velocity

    async def stream(self) -> AsyncIterator[Measurement]:

        while True:
            event_time = datetime.now(UTC)

            position, velocity = self._propagate(event_time)

            noise = self.rng.normal(
                0,
                self.noise_km,
                size=3,
            )

            measured_position = np.array(position) + noise

            measurement = Measurement(
                satellite_id=self.satellite_id,
                source="simulator",
                event_time=event_time,
                fields={
                    "position_x_km": float(measured_position[0]),
                    "position_y_km": float(measured_position[1]),
                    "position_z_km": float(measured_position[2]),
                    "velocity_x_km_s": float(velocity[0]),
                    "velocity_y_km_s": float(velocity[1]),
                    "velocity_z_km_s": float(velocity[2]),
                },
                coordinate_frame="TEME",
                quality_flags=["SIMULATED"],
            )

            yield measurement

            await asyncio.sleep(self.interval_seconds)

    async def health(self) -> dict:
        return {
            "healthy": True,
            "source": "simulator",
            "mode": "OFFLINE_SIMULATION",
        }