from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FlightChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["cancel"]


class FlightAlert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=128)
    fid: str = Field(min_length=1, max_length=64)
    changes: FlightChanges
