"""Unit tests for Data Hygiene, MAD outlier gate, and State Machine."""

from datetime import UTC, datetime, timedelta

import numpy as np

from app.models.measurement import Measurement
from app.pipeline.fault_injector import FaultInjector
from app.pipeline.hygiene import DataHygieneGate
from app.pipeline.state_machine import TwinStateMachine, TwinStatus


def test_hygiene_mad_outlier_rejection():
    """Verify MAD gate rejects sudden sensor spikes but accepts nominal telemetry."""
    gate = DataHygieneGate()
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    nominal_pos = [3800.0, -4200.0, 3500.0]

    # Feed 10 nominal packets to establish baseline
    for i in range(10):
        t = t0 + timedelta(seconds=i)
        meas = Measurement(
            satellite_id=25544,
            source="SIMULATOR",
            event_time=t,
            receive_time=t,
            fields={"position_km": nominal_pos}
        )
        accepted, _, rejections = gate.process(meas)
        assert accepted is True

    # Inject an outlier spike (e.g. 500 km leap)
    t_bad = t0 + timedelta(seconds=11)
    bad_pos = [3800.0 + 500.0, -4200.0, 3500.0]
    bad_meas = Measurement(
        satellite_id=25544,
        source="SIMULATOR",
        event_time=t_bad,
        receive_time=t_bad,
        fields={"position_km": bad_pos}
    )
    accepted, _, rejections = gate.process(bad_meas)
    assert accepted is False
    assert any("MAD_OUTLIER_REJECTED" in r for r in rejections)


def test_state_machine_lifecycle_transitions():
    """Verify LIVE -> STALE -> PREDICTED -> RECOVERED transitions."""
    sm = TwinStateMachine(stale_threshold_seconds=2.0, predicted_threshold_seconds=5.0)
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    # 1. First packet brings it LIVE
    st = sm.update(t0, fresh_packet_arrived=True)
    assert st == TwinStatus.LIVE

    # 2. After 3 seconds without packet -> STALE
    t1 = t0 + timedelta(seconds=3)
    st = sm.update(t1, fresh_packet_arrived=False)
    assert st == TwinStatus.STALE

    # 3. After 7 seconds without packet -> PREDICTED
    t2 = t0 + timedelta(seconds=7)
    st = sm.update(t2, fresh_packet_arrived=False)
    assert st == TwinStatus.PREDICTED

    # 4. Fresh packet arrives -> RECOVERED
    t3 = t0 + timedelta(seconds=8)
    st = sm.update(t3, fresh_packet_arrived=True)
    assert st == TwinStatus.RECOVERED


def test_fault_injector_sever_and_noise():
    """Verify fault injector drops packets on sever_feed and applies noise."""
    fi = FaultInjector()
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    pos = np.array([3800.0, -4200.0, 3500.0])

    # Sever feed test
    fi.update_config(sever_feed=True)
    delivered, p, _, _, tags = fi.process_incoming_packet(pos, None, t0)
    assert delivered is False
    assert "FEED_SEVERED" in tags

    # Noise test
    fi.update_config(sever_feed=False, noise_amplitude_km=2.0)
    delivered, p_noisy, _, _, tags = fi.process_incoming_packet(pos, None, t0)
    assert delivered is True
    diff = np.linalg.norm(p_noisy - pos)
    assert diff > 0.01  # Noise was added
