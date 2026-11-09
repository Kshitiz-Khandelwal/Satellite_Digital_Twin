
from datetime import UTC, datetime

import numpy as np
import pytest

from app.twin.coordinate import CoordinateConverter


def test_teme_to_ecef():

    timestamp = datetime.now(UTC)

    position = np.array([4200.0, 3100.0, 4800.0])
    velocity = np.array([-5.0, 4.0, 3.0])

    ecef_position, ecef_velocity = (
        CoordinateConverter.teme_to_ecef(
            position,
            velocity,
            timestamp,
        )
    )

    assert ecef_position.shape == (3,)
    assert ecef_velocity.shape == (3,)

    assert np.all(np.isfinite(ecef_position))
    assert np.all(np.isfinite(ecef_velocity))

    # Rotation should preserve position magnitude.
    assert np.linalg.norm(ecef_position) == pytest.approx(
        np.linalg.norm(position),
        abs=1e-6,
    )


def test_ecef_to_geodetic():

    # Approximately 400 km above the equator.
    ecef = np.array([6778.137, 0.0, 0.0])

    latitude, longitude, altitude = (
        CoordinateConverter.ecef_to_geodetic(ecef)
    )

    assert latitude == pytest.approx(0.0, abs=1e-6)
    assert longitude == pytest.approx(0.0, abs=1e-6)
    assert altitude == pytest.approx(400.0, abs=1e-3)


def test_geodetic_values_are_valid():

    ecef = np.array([4200.0, 3100.0, 4800.0])

    latitude, longitude, altitude = (
        CoordinateConverter.ecef_to_geodetic(ecef)
    )

    assert -90 <= latitude <= 90
    assert -180 <= longitude <= 180
    assert altitude > 0


def test_invalid_position_rejected():

    with pytest.raises(ValueError):
        CoordinateConverter.ecef_to_geodetic(
            np.array([float("nan"), 0.0, 0.0])
        )


def test_naive_timestamp_rejected():

    with pytest.raises(ValueError):
        CoordinateConverter.teme_to_ecef(
            np.array([4200.0, 3100.0, 4800.0]),
            np.array([1.0, 2.0, 3.0]),
            datetime(2026, 11, 7),
        )
