"""REST API endpoints for Satellite Digital Twin control and telemetry inspection."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.websocket import twin_engine

router = APIRouter(prefix="/api")


class FaultInjectionRequest(BaseModel):
    feed_cut: bool | None = None
    packet_latency_s: float | None = None
    sensor_noise_km: float | None = None
    compare_naive: bool | None = None


@router.get("/status")
@router.get("/telemetry/snapshot")
async def get_system_status() -> dict[str, Any]:
    """Returns current Digital Twin telemetry snapshot and filter health."""
    frame = twin_engine.step()
    return asdict(frame)


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
    """Returns orbital elements and metadata for the tracked spacecraft."""
    return {
        "norad_id": twin_engine.norad_id,
        "name": twin_engine.satellite_name,
        "description": "International Space Station in Low Earth Orbit (LEO)",
        "inclination_deg": 51.64,
        "orbital_period_min": 92.68,
        "apogee_km": 420.0,
        "perigee_km": 415.0,
        "twin_subsystems": ["ORBIT_EKF", "EPS_POWER", "TCS_THERMAL", "ADCS_POINTING", "COMMS_RF"],
    }


@router.get("/anomalies")
async def get_anomalies(limit: int = 20) -> dict[str, Any]:
    """Returns historical and active residual anomaly events."""
    events = twin_engine.anomaly_detector.get_recent_events(limit=limit)
    return {
        "count": len(events),
        "events": [
            {
                "timestamp": e.timestamp.isoformat(),
                "subsystem": e.subsystem,
                "severity": e.severity,
                "code": e.code,
                "message": e.message,
                "residual": e.residual_value,
                "threshold": e.threshold_value,
            }
            for e in events
        ],
    }
