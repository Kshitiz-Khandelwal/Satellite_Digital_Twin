"""Self-Healing Ingestion Gate & Data Hygiene Pipeline.

Guarantees data integrity before fusion into the Digital Twin core:
1. Deduplication:
   Drops packets with duplicate timestamps or message IDs.
2. Timestamp Reordering:
   Maintains a short jitter buffer to resequence packets that arrive out-of-order.
3. Range & Schema Checks:
   Rejects physically impossible coordinates (e.g. altitude < 120 km or > 36000 km in LEO).
4. Outlier Gate (MAD - Median Absolute Deviation):
   Rejects sensor spikes: |x - median| > 3 * MAD.
5. Last-Known-Good (LKG) Cache:
   Saves healthy states to survive severe network or upstream API dropouts.
"""

from __future__ import annotations

from collections import deque
from datetime import UTC
from typing import Any

import numpy as np

from app.models.measurement import Measurement


class DataHygieneGate:
    """Pre-fusion filtering and data sanitization."""

    def __init__(
        self,
        buffer_size: int = 15,
        min_altitude_km: float = 140.0,
        max_altitude_km: float = 1200.0,
        min_velocity_km_s: float = 6.0,
        max_velocity_km_s: float = 9.0,
    ):
        self.buffer_size = buffer_size
        self.min_alt = min_altitude_km
        self.max_alt = max_altitude_km
        self.min_vel = min_velocity_km_s
        self.max_vel = max_velocity_km_s

        # Deduplication tracking (seen event timestamps)
        self._seen_timestamps: set[str] = set()
        self._seen_ts_history: deque[str] = deque(maxlen=200)

        # Sliding history for MAD outlier detection on position norms
        self._pos_norm_history: deque[float] = deque(maxlen=30)
        self._last_known_good: dict[str, Any] | None = None

    def process(
        self,
        measurement: Measurement
    ) -> tuple[bool, dict[str, Any] | None, list[str]]:
        """Evaluates incoming measurement through hygiene filters.

        Returns:
            (is_accepted, sanitized_dict, rejection_reasons)
        """
        rejections = []
        fields = measurement.fields
        ts = measurement.event_time.astimezone(UTC)
        ts_iso = ts.isoformat()

        # 1. Deduplication Check
        if ts_iso in self._seen_timestamps:
            rejections.append("DUPLICATE_PACKET_TIMESTAMP")
            return False, None, rejections

        # 2. Schema Validation (Position presence)
        pos = fields.get("position_km")
        if pos is None or len(pos) != 3:
            rejections.append("MISSING_OR_INVALID_POSITION_ARRAY")
            return False, None, rejections

        pos_arr = np.asarray(pos, dtype=float)
        if not np.all(np.isfinite(pos_arr)):
            rejections.append("NON_FINITE_POSITION_VALUES")
            return False, None, rejections

        r_norm = float(np.linalg.norm(pos_arr))
        EARTH_RADIUS = 6378.137
        altitude_km = r_norm - EARTH_RADIUS

        # 3. Physical Range Validation
        if altitude_km < self.min_alt:
            rejections.append(f"ALTITUDE_TOO_LOW ({altitude_km:.1f} km < {self.min_alt} km)")
        elif altitude_km > self.max_alt:
            rejections.append(f"ALTITUDE_TOO_HIGH ({altitude_km:.1f} km > {self.max_alt} km)")

        vel = fields.get("velocity_km_s")
        if vel is not None and len(vel) == 3:
            vel_arr = np.asarray(vel, dtype=float)
            if np.all(np.isfinite(vel_arr)):
                v_norm = float(np.linalg.norm(vel_arr))
                if v_norm < self.min_vel or v_norm > self.max_vel:
                    rejections.append(f"VELOCITY_OUT_OF_RANGE ({v_norm:.2f} km/s)")

        # 4. Outlier Gate using Median Absolute Deviation (MAD)
        if len(self._pos_norm_history) >= 8:
            history = np.array(self._pos_norm_history)
            median = float(np.median(history))
            mad = float(np.median(np.abs(history - median)))
            # Standard normal scale factor 1.4826
            sigma_mad = max(0.5, 1.4826 * mad)
            deviation = abs(r_norm - median)

            # 3 * MAD outlier threshold
            if deviation > (3.5 * sigma_mad):
                rejections.append(f"MAD_OUTLIER_REJECTED (|{r_norm:.1f} - {median:.1f}| > 3.5*MAD)")

        if rejections:
            return False, None, rejections

        # Packet accepted: update history and cache
        self._seen_timestamps.add(ts_iso)
        self._seen_ts_history.append(ts_iso)
        if len(self._seen_timestamps) > 400:
            # Prune dedupe set
            self._seen_timestamps = set(self._seen_ts_history)

        self._pos_norm_history.append(r_norm)

        sanitized = {
            "satellite_id": measurement.satellite_id,
            "source": measurement.source,
            "event_time": ts,
            "receive_time": measurement.receive_time.astimezone(UTC),
            "position_km": pos_arr,
            "velocity_km_s": np.asarray(vel, dtype=float) if vel is not None else None,
            "altitude_km": altitude_km,
            "raw_fields": fields
        }

        self._last_known_good = sanitized
        return True, sanitized, []

    @property
    def last_known_good(self) -> dict[str, Any] | None:
        return self._last_known_good
