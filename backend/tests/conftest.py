import pytest

from app.models import Booking, Flight


@pytest.fixture(autouse=True)
def disable_external_gemini_for_unit_tests(monkeypatch):
    """Keep normal unit tests offline even on a developer machine with a real key."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    yield


def seed_legacy_test_data(session_factory):
    """Provide the original US fixture for tests that exercise existing workflows."""
    with session_factory.begin() as session:
        if session.query(Flight.id).filter_by(fid="FL-1001").first() is not None:
            return
        session.add_all([
            Flight(fid="FL-1001", status="scheduled"),
            Flight(fid="FL-2002", status="scheduled"),
            Booking(
                booking_ref="BK-100",
                passenger_name="Avery Chen",
                flight_ids=["FL-1001", "FL-2002"],
                itinerary=[
                    {"sequence": 1, "flight_id": "FL-1001", "origin": "SFO", "destination": "LAX",
                     "scheduled_departure": "2026-10-28T09:00:00-07:00", "scheduled_arrival": "2026-10-28T10:30:00-07:00",
                     "departure_timezone": "America/Los_Angeles", "arrival_timezone": "America/Los_Angeles"},
                    {"sequence": 2, "flight_id": "FL-2002", "origin": "LAX", "destination": "JFK",
                     "scheduled_departure": "2026-10-28T12:00:00-07:00", "scheduled_arrival": "2026-10-28T20:30:00-04:00",
                     "departure_timezone": "America/Los_Angeles", "arrival_timezone": "America/New_York"},
                ],
                passenger_constraints={"min_connection_minutes": 60, "max_departure_shift_minutes": 360},
            ),
            Booking(
                booking_ref="BK-101",
                passenger_name="Jordan Patel",
                flight_ids=["FL-1001"],
                itinerary=[{"sequence": 1, "flight_id": "FL-1001", "origin": "SFO", "destination": "LAX",
                            "scheduled_departure": "2026-10-28T09:00:00-07:00", "scheduled_arrival": "2026-10-28T10:30:00-07:00",
                            "departure_timezone": "America/Los_Angeles", "arrival_timezone": "America/Los_Angeles"}],
                passenger_constraints={"max_departure_shift_minutes": 360},
            ),
            Booking(
                booking_ref="BK-102",
                passenger_name="Morgan Lee",
                flight_ids=["FL-2002"],
                itinerary=[{"sequence": 1, "flight_id": "FL-2002", "origin": "LAX", "destination": "JFK",
                            "scheduled_departure": "2026-10-28T12:00:00-07:00", "scheduled_arrival": "2026-10-28T20:30:00-04:00",
                            "departure_timezone": "America/Los_Angeles", "arrival_timezone": "America/New_York"}],
                passenger_constraints={},
            ),
        ])
