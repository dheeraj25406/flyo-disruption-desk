import os
from typing import Optional

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base, Booking, Flight


def make_engine(database_url: Optional[str] = None):
    url = database_url or os.getenv("DATABASE_URL", "sqlite:///./flyo.db")
    options = {"check_same_thread": False, "timeout": 15} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=options)


def make_session_factory(engine):
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


def create_tables(engine) -> None:
    Base.metadata.create_all(engine)
    # create_all does not add columns to an existing Milestone 1 SQLite file.
    if engine.dialect.name == "sqlite":
        columns = {column["name"] for column in inspect(engine).get_columns("bookings")}
        with engine.begin() as connection:
            if "itinerary" not in columns:
                connection.execute(text("ALTER TABLE bookings ADD COLUMN itinerary JSON NOT NULL DEFAULT '[]'"))
            if "passenger_constraints" not in columns:
                connection.execute(text("ALTER TABLE bookings ADD COLUMN passenger_constraints JSON NOT NULL DEFAULT '{}'"))


def seed_demo_data(session_factory) -> None:
    """Create a small synthetic disruption scenario if the database is empty."""
    with session_factory.begin() as session:
        if session.scalar(select(Flight.id).limit(1)) is not None:
            return
        session.add_all([
            Flight(fid="FL-1001", status="scheduled"),
            Flight(fid="FL-2002", status="scheduled"),
            Booking(
                booking_ref="BK-100", passenger_name="Avery Chen", flight_ids=["FL-1001", "FL-2002"],
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
                booking_ref="BK-101", passenger_name="Jordan Patel", flight_ids=["FL-1001"],
                itinerary=[{"sequence": 1, "flight_id": "FL-1001", "origin": "SFO", "destination": "LAX",
                            "scheduled_departure": "2026-10-28T09:00:00-07:00", "scheduled_arrival": "2026-10-28T10:30:00-07:00",
                            "departure_timezone": "America/Los_Angeles", "arrival_timezone": "America/Los_Angeles"}],
                passenger_constraints={"max_departure_shift_minutes": 360},
            ),
            Booking(
                booking_ref="BK-102", passenger_name="Morgan Lee", flight_ids=["FL-2002"],
                itinerary=[{"sequence": 1, "flight_id": "FL-2002", "origin": "LAX", "destination": "JFK",
                            "scheduled_departure": "2026-10-28T12:00:00-07:00", "scheduled_arrival": "2026-10-28T20:30:00-04:00",
                            "departure_timezone": "America/Los_Angeles", "arrival_timezone": "America/New_York"}],
                passenger_constraints={},
            ),
        ])
