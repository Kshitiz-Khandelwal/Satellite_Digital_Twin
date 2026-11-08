
import pytest
import httpx

from app.adapters.celestrak import CelesTrakAdapter


SAMPLE_OMM = [{
    "OBJECT_NAME": "ISS (ZARYA)",
    "OBJECT_ID": "1998-067A",
    "NORAD_CAT_ID": 25544,
    "EPOCH": "2026-11-07T17:00:00",
    "CLASSIFICATION_TYPE": "U",
    "EPHEMERIS_TYPE": 0,
    "ELEMENT_SET_NO": 999,
    "REV_AT_EPOCH": 50000,
    "BSTAR": 0.0001,
    "MEAN_MOTION_DOT": 0.00001,
    "MEAN_MOTION_DDOT": 0,
    "MEAN_MOTION": 15.5,
    "ECCENTRICITY": 0.0005,
    "INCLINATION": 51.64,
    "RA_OF_ASC_NODE": 45.0,
    "ARG_OF_PERICENTER": 30.0,
    "MEAN_ANOMALY": 100.0
}]


@pytest.mark.asyncio
async def test_celestrak_refresh():

    async def handler(request):
        return httpx.Response(
            200,
            json=SAMPLE_OMM,
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )

    adapter = CelesTrakAdapter(client=client)

    try:
        await adapter.refresh()

        assert adapter.satellite is not None
        assert adapter.last_good_data is not None
        assert adapter.last_good_data["NORAD_CAT_ID"] == 25544

        position, velocity = adapter._propagate(
            adapter.last_fetch_time
        )

        assert len(position) == 3
        assert len(velocity) == 3

    finally:
        await client.aclose()


def test_invalid_satellite_id():

    adapter = CelesTrakAdapter()

    with pytest.raises(ValueError):
        adapter._validate_omm({
            **SAMPLE_OMM[0],
            "NORAD_CAT_ID": 12345
        })


def test_missing_omm_fields():

    adapter = CelesTrakAdapter()

    with pytest.raises(ValueError):
        adapter._validate_omm({
            "NORAD_CAT_ID": 25544
        })


def test_invalid_eccentricity():

    adapter = CelesTrakAdapter()

    with pytest.raises(ValueError):
        adapter._validate_omm({
            **SAMPLE_OMM[0],
            "ECCENTRICITY": 1.5
        })
