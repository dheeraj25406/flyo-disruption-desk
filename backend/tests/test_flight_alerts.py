import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import AuditEvent, Booking, DisruptionCase, DisruptionEvent, Flight


def build_client(tmp_path):
    db_path = tmp_path / "test.db"
    app = create_app(f"sqlite:///{db_path}")
    client = TestClient(app)
    client.__enter__()
    return client, app


def close_client(client):
    client.__exit__(None, None, None)


def test_health_returns_success(tmp_path):
    client, _ = build_client(tmp_path)
    try:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
    finally:
        close_client(client)


def test_cancellation_updates_flight_and_only_affected_bookings(tmp_path):
    client, app = build_client(tmp_path)
    try:
        response = client.post("/webhooks/flight-alerts", json={
            "event_id": "evt-cancel-001", "fid": "FL-1001", "changes": {"status": "cancel"}
        })
        assert response.status_code == 200
        assert response.json()["cases_created"] == 2

        with app.state.session_factory() as session:
            flight = session.scalar(select(Flight).where(Flight.fid == "FL-1001"))
            cases = session.scalars(select(DisruptionCase)).all()
            booking_refs = {
                session.get(Booking, case.booking_id).booking_ref for case in cases
            }
            assert flight.status == "cancelled"
            assert len(cases) == 2
            assert booking_refs == {"BK-100", "BK-101"}
            assert session.scalar(select(DisruptionEvent).where(
                DisruptionEvent.event_id == "evt-cancel-001"
            )) is not None
            assert len(session.scalars(select(AuditEvent)).all()) == 3
    finally:
        close_client(client)


def test_replay_does_not_duplicate_event_or_cases(tmp_path):
    client, app = build_client(tmp_path)
    payload = {"event_id": "evt-replay", "fid": "FL-1001", "changes": {"status": "cancel"}}
    try:
        first = client.post("/webhooks/flight-alerts", json=payload)
        replay = client.post("/webhooks/flight-alerts", json=payload)
        assert first.status_code == 200
        assert replay.status_code == 200
        assert replay.json()["status"] == "already_processed"
        assert replay.json()["duplicate"] is True
        assert replay.json()["cases_created"] == 0

        with app.state.session_factory() as session:
            assert len(session.scalars(select(DisruptionEvent)).all()) == 1
            assert len(session.scalars(select(DisruptionCase)).all()) == 2
    finally:
        close_client(client)


def test_unknown_flight_returns_client_error_without_persisting(tmp_path):
    client, app = build_client(tmp_path)
    try:
        response = client.post("/webhooks/flight-alerts", json={
            "event_id": "evt-unknown", "fid": "FL-NOPE", "changes": {"status": "cancel"}
        })
        assert response.status_code == 404
        assert response.json()["detail"] == "Unknown flight: FL-NOPE"
        with app.state.session_factory() as session:
            assert session.scalar(select(DisruptionEvent)) is None
    finally:
        close_client(client)


def test_unsupported_status_is_rejected(tmp_path):
    client, app = build_client(tmp_path)
    try:
        response = client.post("/webhooks/flight-alerts", json={
            "event_id": "evt-delay", "fid": "FL-1001", "changes": {"status": "delay"}
        })
        assert response.status_code == 422
        with app.state.session_factory() as session:
            assert session.scalar(select(DisruptionEvent)) is None
            assert session.scalar(select(Flight).where(Flight.fid == "FL-1001")).status == "scheduled"
    finally:
        close_client(client)


def test_event_id_has_database_uniqueness_constraint(tmp_path):
    client, app = build_client(tmp_path)
    try:
        factory = app.state.session_factory
        with factory.begin() as session:
            session.add(DisruptionEvent(
                event_id="evt-unique", fid="FL-1001", status="cancel", payload={}
            ))
        with pytest.raises(IntegrityError):
            with factory.begin() as session:
                session.add(DisruptionEvent(
                    event_id="evt-unique", fid="FL-1001", status="cancel", payload={}
                ))
    finally:
        close_client(client)
