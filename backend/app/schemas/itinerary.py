from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ItineraryLeg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1)
    flight_id: str = Field(min_length=1)
    origin: Optional[str] = None
    destination: Optional[str] = None
    # Kept as strings at the API boundary so malformed/missing schedules can be
    # reported as review evidence instead of making the whole case unreadable.
    scheduled_departure: Optional[str] = None
    scheduled_arrival: Optional[str] = None
    departure_timezone: Optional[str] = None
    arrival_timezone: Optional[str] = None


class ConnectionAssessment(BaseModel):
    first_flight_id: str
    next_flight_id: str
    status: str
    available_minutes: Optional[int] = None
    minimum_minutes: int
    reason_codes: list[str]
