import os
from typing import Optional

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.demo_data import DEMO_EVENT_PREFIX, demo_disruptions
from app.models import AuditEvent, Base, Booking, DisruptionCase, DisruptionEvent, Flight
from app.models.entities import utcnow


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
    """Add the namespaced synthetic India demo records without replacing existing data."""
    with session_factory.begin() as session:
        demo_events = session.scalars(
            select(DisruptionEvent).where(DisruptionEvent.event_id.like(DEMO_EVENT_PREFIX + "%"))
        ).all()
        existing_event_ids = {event.event_id for event in demo_events}
        seeded_keys = {
            (event.payload or {}).get("demo_key")
            for event in demo_events
            if (event.payload or {}).get("demo_key")
        }
        existing_flights = set(session.scalars(select(Flight.fid)).all())
        existing_bookings = set(session.scalars(select(Booking.booking_ref)).all())

        for record in demo_disruptions():
            if record.event_id in seeded_keys:
                continue
            event_id = _available_id(record.event_id, existing_event_ids)
            flight_id = _available_id(record.flight_id, existing_flights)
            booking_ref = _available_id(record.booking_ref, existing_bookings)

            departure = "{}T09:00:00+05:30".format(record.service_date)
            arrival = "{}T11:00:00+05:30".format(record.service_date)
            itinerary = [{
                "sequence": 1,
                "flight_id": flight_id,
                "origin": record.origin,
                "destination": record.destination,
                "airline_name": record.airline_name,
                "scheduled_departure": departure,
                "scheduled_arrival": arrival,
                "departure_timezone": "Asia/Kolkata",
                "arrival_timezone": "Asia/Kolkata",
            }]
            flight_ids = [flight_id]
            if record.has_tight_connection:
                connection_base = "DEMO-CNX-{}".format(record.event_id[-2:])
                connection_flight = _available_id(connection_base, existing_flights)
                connection_destination = "GOI" if record.destination != "GOI" else "PNQ"
                flight_ids.append(connection_flight)
                itinerary.append({
                    "sequence": 2,
                    "flight_id": connection_flight,
                    "origin": record.destination,
                    "destination": connection_destination,
                    "airline_name": record.airline_name,
                    "scheduled_departure": "{}T11:30:00+05:30".format(record.service_date),
                    "scheduled_arrival": "{}T13:30:00+05:30".format(record.service_date),
                    "departure_timezone": "Asia/Kolkata",
                    "arrival_timezone": "Asia/Kolkata",
                })
                existing_flights.add(connection_flight)
                session.add(Flight(fid=connection_flight, status="scheduled"))

            session.add(Flight(
                fid=flight_id,
                status={
                    "cancel": "cancelled",
                    "delay": "delayed",
                    "missed_connection": "disrupted",
                }[record.status],
            ))
            booking = Booking(
                booking_ref=booking_ref,
                passenger_name=record.passenger_name,
                flight_ids=flight_ids,
                itinerary=itinerary,
                passenger_constraints=record.passenger_constraints,
            )
            session.add(booking)
            session.flush()

            session.add(DisruptionEvent(
                event_id=event_id,
                fid=flight_id,
                status=record.status,
                payload={
                    "demo": True,
                    "synthetic": True,
                    "demo_key": record.event_id,
                    "event_id": event_id,
                    "fid": flight_id,
                    "changes": {"status": record.status},
                    "reason": record.reason,
                },
                received_at=utcnow(),
            ))
            case = DisruptionCase(
                event_id=event_id,
                booking_id=booking.id,
                flight_id=flight_id,
            )
            session.add(case)
            session.flush()
            session.add(AuditEvent(
                event_id=event_id,
                action="demo_disruption_recorded",
                details={
                    "case_id": case.id,
                    "status": record.status,
                    "reason": record.reason,
                    "synthetic": True,
                },
            ))
            session.add(AuditEvent(
                event_id=event_id,
                action="case_created",
                details={"case_id": case.id, "booking_ref": booking.booking_ref, "fid": flight_id},
            ))
            seeded_keys.add(record.event_id)
            existing_event_ids.add(event_id)
            existing_flights.add(flight_id)
            existing_bookings.add(booking_ref)


def _available_id(base: str, existing: set[str]) -> str:
    if base not in existing:
        return base
    suffix = 2
    while "{}-{}".format(base, suffix) in existing:
        suffix += 1
    return "{}-{}".format(base, suffix)
