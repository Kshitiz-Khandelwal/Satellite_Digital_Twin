"""WebSocket Streamer for Live Digital Twin Telemetry.

Streams 1 Hz synchronized telemetry packets containing EKF states,
covariance zones, eclipse geometry, subsystem health, and fault telemetry.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.twin.engine import DigitalTwinEngine

logger = logging.getLogger(__name__)

router = APIRouter()

# Global engine singleton
twin_engine = DigitalTwinEngine()


class ConnectionManager:
    def __init__(self):
        self.active_connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info(f"WebSocket client connected. Total clients: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)
        logger.info(f"WebSocket client disconnected. Total clients: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        dead_connections = set()
        data_str = json.dumps(message)
        for connection in self.active_connections:
            try:
                await connection.send_text(data_str)
            except Exception:
                dead_connections.add(connection)
        for dead in dead_connections:
            self.active_connections.discard(dead)


manager = ConnectionManager()
_broadcast_task: asyncio.Task | None = None


async def telemetry_broadcaster():
    """Background loop generating and streaming 1 Hz Digital Twin telemetry."""
    while True:
        try:
            if manager.active_connections:
                frame = twin_engine.step()
                frame_dict = asdict(frame)
                await manager.broadcast(frame_dict)
            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in telemetry broadcaster loop: {e}", exc_info=True)
            await asyncio.sleep(1.0)


def start_telemetry_loop():
    global _broadcast_task
    if _broadcast_task is None or _broadcast_task.done():
        _broadcast_task = asyncio.create_task(telemetry_broadcaster())


@router.websocket("/ws/telemetry")
async def websocket_telemetry_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    start_telemetry_loop()
    try:
        # Send an immediate snapshot right upon connection
        initial_frame = asdict(twin_engine.step())
        await websocket.send_text(json.dumps(initial_frame))

        while True:
            # Keep connection open and listen for any client command messages
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                cmd = msg.get("command")
                if cmd == "SET_FAULT":
                    twin_engine.set_fault(
                        feed_cut=msg.get("feed_cut"),
                        packet_latency_s=msg.get("packet_latency_s"),
                        sensor_noise_km=msg.get("sensor_noise_km"),
                        compare_naive=msg.get("compare_naive"),
                    )
                elif cmd == "RESET_FAULTS":
                    twin_engine.reset_faults()
            except Exception as e:
                logger.warning(f"Error handling websocket client message: {e}")
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.warning(f"WebSocket connection error: {e}")
        manager.disconnect(websocket)
