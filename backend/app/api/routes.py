"""REST API endpoints for Satellite Digital Twin control and telemetry inspection."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.api.websocket import twin_engine
from app.models.catalog import SATELLITE_CATALOG

router = APIRouter(prefix="/api")


class FaultInjectionRequest(BaseModel):
    feed_cut: bool | None = None
    packet_latency_s: float | None = None
    sensor_noise_km: float | None = None
    compare_naive: bool | None = None


@router.get("/status")
@router.get("/telemetry/snapshot")
async def get_system_status(
    norad_id: int | None = Query(default=None),
    feed_cut: bool | None = Query(default=None),
    outage_s: float | None = Query(default=None),
    packet_latency_s: float | None = Query(default=None),
    sensor_noise_km: float | None = Query(default=None),
    compare_naive: bool | None = Query(default=None),
    include_track: bool = Query(default=True),
) -> dict[str, Any]:
    """Returns current Digital Twin telemetry snapshot and filter health.

    Supports stateless query parameters so distributed serverless environments (e.g. Vercel)
    stay deterministically synchronized with client-selected targets and fault simulations.
    """
    if norad_id is not None and norad_id != twin_engine.norad_id:
        twin_engine.select_satellite(norad_id)

    if any(v is not None for v in (feed_cut, packet_latency_s, sensor_noise_km, compare_naive)):
        twin_engine.set_fault(
            feed_cut=feed_cut,
            packet_latency_s=packet_latency_s,
            sensor_noise_km=sensor_noise_km,
            compare_naive=compare_naive,
        )

    frame = twin_engine.step(
        outage_override_s=outage_s,
        include_orbit_track=include_track,
    )
    return asdict(frame)


@router.post("/select-satellite/{norad_id}")
async def select_satellite(norad_id: int) -> dict[str, Any]:
    """Switches the tracked satellite target and re-bootstraps SGP4/EKF models."""
    spec = twin_engine.select_satellite(norad_id)
    frame = twin_engine.step(include_orbit_track=True)
    return {
        "status": "success",
        "norad_id": spec.norad_id,
        "name": spec.name,
        "inclination_deg": spec.inclination_deg,
        "orbital_period_min": spec.orbital_period_min,
        "nominal_altitude_km": spec.nominal_altitude_km,
        "description": spec.description,
        "telemetry": asdict(frame),
    }


@router.get("/satellites")
async def list_satellites() -> dict[str, Any]:
    """Lists all configured satellite targets in the mission catalog."""
    return {
        "satellites": [asdict(spec) for spec in SATELLITE_CATALOG.values()],
        "active_norad_id": twin_engine.norad_id,
    }


@router.post("/faults/inject")
async def inject_fault(request: FaultInjectionRequest) -> dict[str, Any]:
    """Injects simulated network or sensor anomalies into the twin."""
    twin_engine.set_fault(
        feed_cut=request.feed_cut,
        packet_latency_s=request.packet_latency_s,
        sensor_noise_km=request.sensor_noise_km,
        compare_naive=request.compare_naive,
    )
    return {
        "status": "success",
        "active_faults": asdict(twin_engine.faults),
    }


@router.post("/faults/reset")
async def reset_faults() -> dict[str, Any]:
    """Clears all active fault injections and restores nominal streaming."""
    twin_engine.reset_faults()
    return {
        "status": "success",
        "message": "All faults cleared. Nominal state restored.",
        "active_faults": asdict(twin_engine.faults),
    }


@router.get("/satellite")
async def get_satellite_info() -> dict[str, Any]:
    """Returns orbital elements and metadata for the currently tracked spacecraft."""
    spec = twin_engine.spec
    return {
        "norad_id": spec.norad_id,
        "name": spec.name,
        "description": spec.description,
        "inclination_deg": spec.inclination_deg,
        "orbital_period_min": spec.orbital_period_min,
        "apogee_km": round(spec.nominal_altitude_km + 2.5, 1),
        "perigee_km": round(spec.nominal_altitude_km - 2.5, 1),
        "twin_subsystems": ["ORBIT_EKF", "EPS_POWER", "TCS_THERMAL", "ADCS_POINTING", "COMMS_RF"],
    }


@router.get("/anomalies")
async def get_anomalies(limit: int = 20) -> dict[str, Any]:
    """Returns historical and active residual anomaly events."""
    events = twin_engine.anomaly_detector.get_recent_events(limit=limit)
    return {
        "count": len(events),
        "events": events,
    }
