import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import AuditEvent, Booking, DisruptionCase, DisruptionEvent, Flight
from conftest import seed_legacy_test_data


def build_client(tmp_path):
    db_path = tmp_path / "test.db"
    app = create_app(f"sqlite:///{db_path}", seed_data=False)
    client = TestClient(app)
    client.__enter__()
    seed_legacy_test_data(app.state.session_factory)
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


def test_indian_demo_seed_creates_25_unique_related_cases_and_is_idempotent(tmp_path):
    db_path = tmp_path / "india-demo.db"
    app = create_app(f"sqlite:///{db_path}")
    client = TestClient(app)
    client.__enter__()
    try:
        cases = client.get("/cases").json()
        assert len(cases) == 25
        assert len({case["id"] for case in cases}) == 25
        assert len({case["booking_ref"] for case in cases}) == 25
        assert len({case["flight_id"] for case in cases}) == 25
        assert {case["passenger_name"] for case in cases} >= {"Aarav Sharma", "Priya Reddy", "Arjun Patel"}

        with app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(DisruptionEvent).where(
                DisruptionEvent.event_id.like("demo-india-case-%")
            )) == 25
            assert session.scalar(select(func.count()).select_from(DisruptionCase)) == 25
            event_statuses = set()
            for case_item in cases:
                case = session.get(DisruptionCase, case_item["id"])
                booking = session.get(Booking, case.booking_id)
                event = session.scalar(select(DisruptionEvent).where(
                    DisruptionEvent.event_id == case.event_id
                ))
                assert booking.booking_ref == case_item["booking_ref"]
                assert case.flight_id in booking.flight_ids
                assert {leg["flight_id"] for leg in booking.itinerary} == set(booking.flight_ids)
                assert all(leg["airline_name"] for leg in booking.itinerary)
                assert all(session.scalar(select(Flight.id).where(Flight.fid == fid)) is not None
                           for fid in booking.flight_ids)
                assert event.fid == case.flight_id
                assert event.payload["synthetic"] is True
                event_statuses.add(event.status)
            assert {"cancel", "delay", "missed_connection"} <= event_statuses
            airports = {
                airport
                for booking in session.scalars(select(Booking)).all()
                for leg in booking.itinerary
                for airport in (leg["origin"], leg["destination"])
            }
            assert {"HYD", "DEL", "BLR", "BOM", "MAA", "CCU", "COK", "PNQ", "AMD", "GOI"} <= airports

        sample = next(item for item in cases if item["booking_ref"] == "INDEMO0001")
        sample_detail = client.get(f"/cases/{sample['id']}").json()
        assert sample_detail["disruption_reason"] == "Synthetic operational cancellation"
        assert sample_detail["itinerary"][0]["airline_name"] == "Air India"
        for booking_ref, expected_decision, expect_candidate in (
            ("INDEMO0001", "recommend_for_review", True),
            ("INDEMO0004", "manual_review", False),
            ("INDEMO0009", "escalate", False),
            ("INDEMO0023", "manual_review", False),
        ):
            case_id = next(item["id"] for item in cases if item["booking_ref"] == booking_ref)
            evaluation = client.post(f"/cases/{case_id}/evaluate")
            assert evaluation.status_code == 200
            body = evaluation.json()
            assert body["decision_code"] == expected_decision, booking_ref
            assert bool(body["candidates"]) is expect_candidate
            assert body["requires_human_approval"] is True
            if expect_candidate:
                assert all(candidate["simulated"] for candidate in body["candidates"])
                assert all((candidate["origin"], candidate["destination"]) == ("HYD", "DEL")
                           for candidate in body["candidates"])
            if expected_decision == "escalate":
                assert body["connection_assessments"][0]["status"] == "insufficient"
    finally:
        close_client(client)

    restarted_app = create_app(f"sqlite:///{db_path}")
    restarted_client = TestClient(restarted_app)
    restarted_client.__enter__()
    try:
        assert len(restarted_client.get("/cases").json()) == 25
        with restarted_app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(DisruptionEvent).where(
                DisruptionEvent.event_id.like("demo-india-case-%")
            )) == 25
    finally:
        close_client(restarted_client)
