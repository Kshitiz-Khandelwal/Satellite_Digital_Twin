import asyncio
import math

import pytest

from app.adapters.simulator import SimulatorAdapter
from app.models.measurement import Measurement


def test_simulator_generates_measurement():
    async def run():
        adapter = SimulatorAdapter(interval_seconds=0.01)

        stream = adapter.stream()

        measurement = await anext(stream)

        await stream.aclose()

        assert isinstance(measurement, Measurement)
        assert measurement.satellite_id == 25544
        assert measurement.source == "simulator"
        assert measurement.coordinate_frame == "TEME"

        assert "SIMULATED" in measurement.quality_flags

        for key in [
            "position_x_km",
            "position_y_km",
            "position_z_km",
            "velocity_x_km_s",
            "velocity_y_km_s",
            "velocity_z_km_s",
        ]:
            assert math.isfinite(measurement.fields[key])

    asyncio.run(run())


def test_simulator_health():
    async def run():
        adapter = SimulatorAdapter()
        health = await adapter.health()

        assert health["healthy"] is True
        assert health["mode"] == "OFFLINE_SIMULATION"

    asyncio.run(run())


def test_invalid_simulator_configuration():
    with pytest.raises(ValueError):
        SimulatorAdapter(interval_seconds=0)

    with pytest.raises(ValueError):
        SimulatorAdapter(noise_km=-1)


def test_naive_timestamp_rejected():
    from datetime import datetime

    with pytest.raises(ValueError):
        Measurement(
            satellite_id=25544,
            source="test",
            event_time=datetime(2026, 11, 7),
            fields={},
        )