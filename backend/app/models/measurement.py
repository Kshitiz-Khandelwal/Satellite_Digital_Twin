from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, field_validator


class Measurement(BaseModel):
    satellite_id: int
    source: str

    event_time: datetime
    receive_time: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    fields: dict[str, Any]

    coordinate_frame: str | None = None
    quality_flags: list[str] = Field(default_factory=list)

    @field_validator("event_time", "receive_time")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Timestamp must include timezone information")

        return value.astimezone(timezone.utc)