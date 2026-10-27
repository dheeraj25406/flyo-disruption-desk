from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.schemas.itinerary import ConnectionAssessment, ItineraryLeg
from app.services.policy import RecoveryPolicy


RISKY_STATUSES = {"needs_review", "route_mismatch", "impossible", "insufficient", "tight"}


def parse_zoned_instant(value: Optional[str], zone_name: Optional[str]):
    if not value or not zone_name:
        return None
    try:
        ZoneInfo(zone_name)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, ZoneInfoNotFoundError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    # Validate the named timezone and compare absolute instants, never local strings.
    return parsed.astimezone(timezone.utc)


def assess_connections(legs: list[ItineraryLeg], policy: RecoveryPolicy) -> list[ConnectionAssessment]:
    ordered = sorted(legs, key=lambda leg: leg.sequence)
    assessments = []
    for first, second in zip(ordered, ordered[1:]):
        reasons = []
        available = None
        status = "feasible"
        if not first.origin or not first.destination or not second.origin or not second.destination:
            status = "needs_review"
            reasons.append("airport_data_missing")
        elif first.destination != second.origin:
            status = "route_mismatch"
            reasons.append("connection_airports_do_not_match")

        arrival_zone_ok = _valid_zone(first.arrival_timezone)
        departure_zone_ok = _valid_zone(second.departure_timezone)
        if not arrival_zone_ok or not departure_zone_ok:
            status = "needs_review"
            reasons.append("timezone_missing_or_invalid")
        arrival = parse_zoned_instant(first.scheduled_arrival, first.arrival_timezone)
        departure = parse_zoned_instant(second.scheduled_departure, second.departure_timezone)
        if arrival is None or departure is None:
            status = "needs_review"
            reasons.append("schedule_missing_or_invalid")
        elif status != "route_mismatch":
            available = int((departure - arrival).total_seconds() // 60)
            if available < 0:
                status = "impossible"
                reasons.append("connection_departure_precedes_arrival")
            elif available < policy.minimum_connection_minutes:
                status = "insufficient"
                reasons.append("below_minimum_connection_buffer")
            elif available < policy.minimum_connection_minutes + policy.tight_connection_window_minutes:
                status = "tight"
                reasons.append("tight_connection_buffer")
            else:
                reasons.append("connection_buffer_meets_configured_threshold")

        assessments.append(ConnectionAssessment(
            first_flight_id=first.flight_id,
            next_flight_id=second.flight_id,
            status=status,
            available_minutes=available,
            minimum_minutes=policy.minimum_connection_minutes,
            reason_codes=reasons,
        ))
    return assessments


def _valid_zone(value: Optional[str]) -> bool:
    if not value:
        return False
    try:
        ZoneInfo(value)
        return True
    except (ValueError, ZoneInfoNotFoundError):
        return False
