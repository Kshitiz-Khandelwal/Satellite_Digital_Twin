from pydantic import BaseModel, Field


class SatelliteMetadata(BaseModel):
    norad_id: int = Field(gt=0)
    name: str
    orbit_source: str
    coordinate_frame: str = "TEME"
    tle_age_seconds: float | None = None