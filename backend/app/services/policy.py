"""Deterministic recovery policy and explicitly synthetic alternative inventory."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RecoveryPolicy:
    policy_id: str = "synthetic-recovery"
    version: str = "1.0"
    minimum_connection_minutes: int = 60
    tight_connection_window_minutes: int = 30
    max_departure_shift_minutes: int = 360
    max_arrival_delay_minutes: int = 240
    departure_shift_weight: int = 1
    arrival_delay_weight: int = 1


@dataclass(frozen=True)
class Alternative:
    inventory_id: str
    flight_id: str
    origin: str
    destination: str
    scheduled_departure: str
    scheduled_arrival: str
    departure_timezone: str
    arrival_timezone: str
    available: bool


DEFAULT_POLICY = RecoveryPolicy()
SYNTHETIC_INVENTORY = (
    Alternative("SIM-001", "SIM-FL-501", "SFO", "LAX", "2026-10-28T09:00:00-07:00", "2026-10-28T10:00:00-07:00", "America/Los_Angeles", "America/Los_Angeles", True),
    Alternative("SIM-002", "SIM-FL-502", "SFO", "LAX", "2026-10-28T11:30:00-07:00", "2026-10-28T12:45:00-07:00", "America/Los_Angeles", "America/Los_Angeles", True),
    Alternative("SIM-003", "SIM-FL-503", "SJC", "LAX", "2026-10-28T09:05:00-07:00", "2026-10-28T10:05:00-07:00", "America/Los_Angeles", "America/Los_Angeles", True),
    Alternative("SIM-004", "SIM-FL-504", "SFO", "LAX", "2026-10-28T09:15:00-07:00", "2026-10-28T10:15:00-07:00", "America/Los_Angeles", "America/Los_Angeles", False),
)
