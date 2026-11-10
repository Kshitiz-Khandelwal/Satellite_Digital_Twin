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

        # Target switching (NOAA-20 polar sun-synchronous 824 km orbit)
        resp_switch = await client.post("/api/select-satellite/43013")
        assert resp_switch.status_code == 200
        sw_data = resp_switch.json()
        assert sw_data["norad_id"] == 43013
        assert sw_data["inclination_deg"] == pytest.approx(98.74, abs=0.1)
        assert sw_data["telemetry"]["position_geodetic"]["altitude_km"] > 700.0
        assert len(sw_data["telemetry"]["orbit_track"]) > 50

        # Stateless serverless fault propagation snapshot check
        resp_stateless = await client.get("/api/telemetry/snapshot?norad_id=20580&feed_cut=true&outage_s=12.0")
        assert resp_stateless.status_code == 200
        sl_data = resp_stateless.json()
        assert sl_data["norad_id"] == 20580
        assert sl_data["twin_status"] == "PREDICTED"
        assert sl_data["ekf_is_propagated_only"] is True
        assert sl_data["ekf_uncertainty_radius_km"] > 1.0
        assert sl_data["naive_position"]["drift_km"] > 20.0

        # Stateless 4s packet delay check
        resp_delay = await client.get("/api/telemetry/snapshot?norad_id=25544&feed_cut=false&packet_latency_s=4.0&sensor_noise_km=0.0")
        assert resp_delay.status_code == 200
        dl_data = resp_delay.json()
        assert dl_data["twin_status"] == "DELAYED"
        assert dl_data["comms"]["latency_ms"] >= 4000.0
        assert dl_data["delayed_position"] is not None
        assert dl_data["delayed_position"]["lag_distance_km"] > 10.0

        # Stateless MAD outlier spike rejection check
        resp_spike = await client.get("/api/telemetry/snapshot?norad_id=48274&feed_cut=false&packet_latency_s=0.0&sensor_noise_km=55.0")
        assert resp_spike.status_code == 200
        sp_data = resp_spike.json()
        assert sp_data["norad_id"] == 48274
        assert sp_data["rejected_spike"] is not None
        assert sp_data["rejected_spike"]["error_km"] > 25.0

        # Frontend dashboard serves updated HMI
        resp_home = await client.get("/")
        assert resp_home.status_code == 200
        assert "Follow Target (F)" in resp_home.text
        assert "Sever Telemetry Feed" in resp_home.text

        # Anomalies endpoint
        resp_anom = await client.get("/api/anomalies")
        assert resp_anom.status_code == 200
        assert "events" in resp_anom.json()

        # Restore back to ISS 25544 and reset faults cleanly
        await client.post("/api/select-satellite/25544")
        await client.post("/api/faults/reset")
