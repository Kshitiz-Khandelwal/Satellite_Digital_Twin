
import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import numpy as np
from sgp4.api import Satrec, jday
from sgp4.omm import initialize

from app.models.measurement import Measurement

logger = logging.getLogger(__name__)


class CelesTrakAdapter:

    BASE_URL = "https://celestrak.org/NORAD/elements/gp.php"

    # Fields required by the SGP4 OMM initializer.
    REQUIRED_FIELDS = {
        "OBJECT_ID",
        "NORAD_CAT_ID",
        "EPOCH",
        "CLASSIFICATION_TYPE",
        "EPHEMERIS_TYPE",
        "ELEMENT_SET_NO",
        "REV_AT_EPOCH",
        "BSTAR",
        "MEAN_MOTION_DOT",
        "MEAN_MOTION_DDOT",
        "MEAN_MOTION",
        "ECCENTRICITY",
        "INCLINATION",
        "RA_OF_ASC_NODE",
        "ARG_OF_PERICENTER",
        "MEAN_ANOMALY",
    }

    NUMERIC_FIELDS = {
        "NORAD_CAT_ID",
        "EPHEMERIS_TYPE",
        "ELEMENT_SET_NO",
        "REV_AT_EPOCH",
        "BSTAR",
        "MEAN_MOTION_DOT",
        "MEAN_MOTION_DDOT",
        "MEAN_MOTION",
        "ECCENTRICITY",
        "INCLINATION",
        "RA_OF_ASC_NODE",
        "ARG_OF_PERICENTER",
        "MEAN_ANOMALY",
    }

    def __init__(
        self,
        satellite_id: int = 25544,
        refresh_seconds: int = 7200,
        interval_seconds: float = 1.0,
        client: httpx.AsyncClient | None = None,
    ):

        if refresh_seconds < 7200:
            raise ValueError(
                "CelesTrak refresh must respect its 2-hour policy"
            )

        if interval_seconds <= 0:
            raise ValueError("Interval must be positive")

        self.satellite_id = satellite_id
        self.refresh_seconds = refresh_seconds
        self.interval_seconds = interval_seconds

        self.client = client or httpx.AsyncClient(timeout=15.0)
        self._owns_client = client is None

        self.satellite: Satrec | None = None
        self.last_good_data: dict | None = None
        self.last_fetch_time: datetime | None = None
        self.last_error: str | None = None

    async def close(self):
        if self._owns_client:
            await self.client.aclose()

    def _validate_omm(self, data: dict) -> dict:

        missing = self.REQUIRED_FIELDS - data.keys()

        if missing:
            raise ValueError(
                f"Missing OMM fields: {sorted(missing)}"
            )

        clean = dict(data)

        if int(clean["NORAD_CAT_ID"]) != self.satellite_id:
            raise ValueError("Unexpected satellite ID")

        if not clean["OBJECT_ID"]:
            raise ValueError("OBJECT_ID cannot be empty")

        if not clean["CLASSIFICATION_TYPE"]:
            raise ValueError("Missing classification")

        for field in self.NUMERIC_FIELDS:
            try:
                value = float(clean[field])
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    f"Invalid numeric OMM field: {field}"
                ) from exc

            if not np.isfinite(value):
                raise ValueError(
                    f"Non-finite OMM value: {field}"
                )

            clean[field] = value

        if clean["MEAN_MOTION"] <= 0:
            raise ValueError("Mean motion must be positive")

        if not 0 <= clean["ECCENTRICITY"] < 1:
            raise ValueError("Invalid eccentricity")

        if not 0 <= clean["INCLINATION"] <= 180:
            raise ValueError("Invalid inclination")

        try:
            epoch = datetime.fromisoformat(
                str(clean["EPOCH"]).replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ValueError("Invalid OMM epoch") from exc

        if epoch.tzinfo is None:
            epoch = epoch.replace(tzinfo=UTC)

        epoch = epoch.astimezone(UTC)

        # sgp4.omm.initialize expects fractional seconds
        # without a timezone suffix.
        clean["EPOCH"] = epoch.strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        )

        return clean

    async def refresh(self):

        now = datetime.now(UTC)

        if (
            self.satellite is not None
            and self.last_fetch_time is not None
            and (
                now - self.last_fetch_time
            ).total_seconds() < self.refresh_seconds
        ):
            return

        try:
            response = await self.client.get(
                self.BASE_URL,
                params={
                    "CATNR": self.satellite_id,
                    "FORMAT": "JSON",
                },
            )

            response.raise_for_status()

            payload = response.json()

            if not isinstance(payload, list) or len(payload) != 1:
                raise ValueError(
                    "Unexpected CelesTrak response"
                )

            clean = self._validate_omm(payload[0])

            # Build a new satellite object first.
            # Replace the cached object only after successful initialization.
            satellite = Satrec()
            initialize(satellite, clean)

            self.satellite = satellite
            self.last_good_data = clean
            self.last_fetch_time = now
            self.last_error = None

            logger.info(
                "CelesTrak orbital elements updated",
                extra={
                    "satellite_id": self.satellite_id,
                    "source": "celestrak",
                },
            )

        except (
            httpx.HTTPError,
            ValueError,
            TypeError,
            RuntimeError,
            KeyError,
        ) as exc:

            self.last_error = str(exc)

            logger.exception(
                "CelesTrak refresh failed",
                extra={
                    "satellite_id": self.satellite_id
                },
            )

            if self.satellite is None:
                raise RuntimeError(
                    "No valid CelesTrak data or cached orbit available"
                ) from exc

            logger.warning(
                "Using last known good orbital elements",
                extra={
                    "satellite_id": self.satellite_id
                },
            )

    def _propagate(self, timestamp: datetime):

        if self.satellite is None:
            raise RuntimeError(
                "No valid orbital elements available"
            )

        if timestamp.tzinfo is None:
            raise ValueError(
                "Propagation timestamp must be timezone-aware"
            )

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

        return position, velocity

    async def stream(self) -> AsyncIterator[Measurement]:

        while True:

            await self.refresh()

            event_time = datetime.now(UTC)

            position, velocity = self._propagate(event_time)

            yield Measurement(
                satellite_id=self.satellite_id,
                source="celestrak",
                event_time=event_time,
                fields={
                    "position_x_km": float(position[0]),
                    "position_y_km": float(position[1]),
                    "position_z_km": float(position[2]),
                    "velocity_x_km_s": float(velocity[0]),
                    "velocity_y_km_s": float(velocity[1]),
                    "velocity_z_km_s": float(velocity[2]),
                },
                coordinate_frame="TEME",
                quality_flags=[
                    "ORBITAL_ELEMENTS",
                    "SGP4_PROPAGATED",
                ],
            )

            await asyncio.sleep(self.interval_seconds)

    async def health(self) -> dict:

        return {
            "healthy": self.satellite is not None,
            "source": "celestrak",
            "satellite_id": self.satellite_id,
            "last_fetch_time": (
                self.last_fetch_time.isoformat()
                if self.last_fetch_time
                else None
            ),
            "cached": self.satellite is not None,
            "last_error": self.last_error,
        }
