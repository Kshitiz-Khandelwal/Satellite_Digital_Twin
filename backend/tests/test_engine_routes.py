"""Unit and integration tests for DigitalTwinEngine and FastAPI endpoints."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.twin.engine import DigitalTwinEngine


def test_engine_step_produces_valid_telemetry_frame():
    """Verify that DigitalTwinEngine produces a complete, populated frame."""
    engine = DigitalTwinEngine(norad_id=25544, satellite_name="ISS (ZARYA)")
    frame = engine.step()

    assert frame.norad_id == 25544
    assert frame.satellite_name == "ISS (ZARYA)"
    assert frame.twin_status in ("LIVE", "RECOVERED", "PREDICTED", "STALE", "DELAYED")
    assert frame.ekf_confidence_pct >= 0.0 and frame.ekf_confidence_pct <= 100.0
    assert frame.ekf_uncertainty_radius_km > 0.0

    # Geodetic coordinates
    geo = frame.position_geodetic
    assert -90.0 <= geo["latitude"] <= 90.0
    assert -180.0 <= geo["longitude"] <= 180.0
    assert 200.0 <= geo["altitude_km"] <= 800.0
    assert 6.0 <= geo["velocity_km_s"] <= 9.0

    # Subsystem physics
    assert "status" in frame.eclipse
    assert "solar_generation_kw" in frame.power
    assert "core_temp_c" in frame.thermal
    assert "latency_ms" in frame.comms


def test_engine_fault_injection_feed_severance():
    """Verify that severing the feed forces twin into PREDICTED state with expanding covariance."""
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    engine = DigitalTwinEngine(norad_id=25544)
    initial_frame = engine.step(current_time=t0)
    initial_cov = initial_frame.ekf_uncertainty_radius_km

    # Sever feed and advance clock by 10 seconds (1s per step)
    engine.set_fault(feed_cut=True)
    frame = initial_frame
    for i in range(1, 10):
        t = t0 + timedelta(seconds=i)
        frame = engine.step(current_time=t)

    assert frame.twin_status in ("STALE", "PREDICTED")
    assert frame.ekf_is_propagated_only is True
    assert frame.ekf_uncertainty_radius_km >= initial_cov


@pytest.mark.asyncio
async def test_fastapi_rest_endpoints():
    """Test REST endpoints: /health, /api/status, /api/satellite, and /api/faults."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Health check
        resp_health = await client.get("/health")
        assert resp_health.status_code == 200
        assert resp_health.json()["status"] == "healthy"

        # Status snapshot
        resp_status = await client.get("/api/status")
        assert resp_status.status_code == 200
        data = resp_status.json()
        assert "twin_status" in data
        assert "position_geodetic" in data

        # Satellite metadata
        resp_sat = await client.get("/api/satellite")
        assert resp_sat.status_code == 200
        assert resp_sat.json()["norad_id"] == 25544

        # Fault injection
        resp_fault = await client.post("/api/faults/inject", json={"feed_cut": True})
        assert resp_fault.status_code == 200
        assert resp_fault.json()["active_faults"]["feed_cut"] is True

        # Fault reset
        resp_reset = await client.post("/api/faults/reset")
        assert resp_reset.status_code == 200
        assert resp_reset.json()["active_faults"]["feed_cut"] is False
