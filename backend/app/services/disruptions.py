from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent, Booking, DisruptionCase, DisruptionEvent, Flight
from app.models.entities import utcnow
from app.schemas.flight_alert import FlightAlert


class UnknownFlightError(Exception):
    pass


def process_cancellation(session: Session, alert: FlightAlert) -> tuple[bool, int]:
    """Process an alert atomically; return (duplicate, number of new cases)."""
    existing = session.scalar(
        select(DisruptionEvent).where(DisruptionEvent.event_id == alert.event_id)
    )
    if existing is not None:
        return True, 0

    flight = session.scalar(select(Flight).where(Flight.fid == alert.fid))
    if flight is None:
        raise UnknownFlightError(alert.fid)

    event = DisruptionEvent(
        event_id=alert.event_id,
        fid=alert.fid,
        status=alert.changes.status,
        payload=alert.model_dump(mode="json"),
        received_at=utcnow(),
    )
    session.add(event)
    flight.status = "cancelled"
    session.add(AuditEvent(
        event_id=alert.event_id,
        action="flight_cancelled",
        details={"fid": alert.fid},
        created_at=utcnow(),
    ))

    bookings = session.scalars(select(Booking)).all()
    cases_created = 0
    for booking in bookings:
        if alert.fid in booking.flight_ids:
            session.add(DisruptionCase(
                event_id=alert.event_id,
                booking_id=booking.id,
                flight_id=alert.fid,
            ))
            session.add(AuditEvent(
                event_id=alert.event_id,
                action="case_created",
                details={"booking_ref": booking.booking_ref, "fid": alert.fid},
            ))
            cases_created += 1

    session.flush()
    return False, cases_created
