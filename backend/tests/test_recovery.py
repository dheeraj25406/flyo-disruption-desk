from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import create_app
from app.models import AuditEvent, Booking, DisruptionCase, DisruptionEvent, RecoveryDecision
from app.schemas.itinerary import ItineraryLeg
from app.services.policy import Alternative, DEFAULT_POLICY
from app.services.risk import assess_connections
from conftest import seed_legacy_test_data


def setup_case(tmp_path, booking_ref="BK-100"):
    app = create_app("sqlite:///{}".format(tmp_path / "recovery.db"), seed_data=False)
    client = TestClient(app)
    client.__enter__()
    seed_legacy_test_data(app.state.session_factory)
    response = client.post("/webhooks/flight-alerts", json={
        "event_id": "evt-risk", "fid": "FL-1001", "changes": {"status": "cancel"}
    })
    assert response.status_code == 200
    with app.state.session_factory() as session:
        case = session.scalar(select(DisruptionCase).join(Booking).where(Booking.booking_ref == booking_ref))
        return client, app, case.id


def teardown_case(client):
    client.__exit__(None, None, None)


def test_itinerary_connection_with_configured_buffer_is_feasible():
    legs = [
        ItineraryLeg(sequence=1, flight_id="A", origin="SFO", destination="LAX",
                    scheduled_departure="2026-10-28T09:00:00-07:00", scheduled_arrival="2026-10-28T10:00:00-07:00",
                    departure_timezone="America/Los_Angeles", arrival_timezone="America/Los_Angeles"),
        ItineraryLeg(sequence=2, flight_id="B", origin="LAX", destination="JFK",
                    scheduled_departure="2026-10-28T12:00:00-07:00", scheduled_arrival="2026-10-28T20:00:00-04:00",
                    departure_timezone="America/Los_Angeles", arrival_timezone="America/New_York"),
    ]
    assessment = assess_connections(legs, DEFAULT_POLICY)[0]
    assert assessment.status == "feasible"
    assert assessment.available_minutes == 120


def test_connection_below_minimum_buffer_is_insufficient():
    legs = [
        ItineraryLeg(sequence=1, flight_id="A", origin="SFO", destination="LAX",
                    scheduled_departure="2026-10-28T09:00:00-07:00", scheduled_arrival="2026-10-28T10:30:00-07:00",
                    departure_timezone="America/Los_Angeles", arrival_timezone="America/Los_Angeles"),
        ItineraryLeg(sequence=2, flight_id="B", origin="LAX", destination="JFK",
                    scheduled_departure="2026-10-28T11:00:00-07:00", scheduled_arrival="2026-10-28T19:00:00-04:00",
                    departure_timezone="America/Los_Angeles", arrival_timezone="America/New_York"),
    ]
    assessment = assess_connections(legs, DEFAULT_POLICY)[0]
    assert assessment.status == "insufficient"
    assert assessment.available_minutes == 30
    assert "below_minimum_connection_buffer" in assessment.reason_codes


def test_missing_or_invalid_timezone_requires_review():
    legs = [
        ItineraryLeg(sequence=1, flight_id="A", origin="SFO", destination="LAX",
                    scheduled_departure="2026-10-28T09:00:00-07:00", scheduled_arrival="2026-10-28T10:00:00-07:00",
                    departure_timezone="America/Los_Angeles", arrival_timezone="Not/A_Zone"),
        ItineraryLeg(sequence=2, flight_id="B", origin="LAX", destination="JFK",
                    scheduled_departure="2026-10-28T12:00:00-07:00", scheduled_arrival="2026-10-28T20:00:00-04:00",
                    departure_timezone="America/Los_Angeles", arrival_timezone="America/New_York"),
    ]
    assessment = assess_connections(legs, DEFAULT_POLICY)[0]
    assert assessment.status == "needs_review"
    assert "timezone_missing_or_invalid" in assessment.reason_codes


def test_incompatible_alternative_route_is_rejected(tmp_path):
    client, app, case_id = setup_case(tmp_path, "BK-101")
    incompatible = Alternative("ONLY-WRONG", "SIM-WRONG", "SJC", "LAX", "2026-10-28T09:00:00-07:00",
                               "2026-10-28T10:00:00-07:00", "America/Los_Angeles", "America/Los_Angeles", True)
    try:
        with app.state.session_factory.begin() as session:
            case = session.get(DisruptionCase, case_id)
            booking = session.get(Booking, case.booking_id)
            from app.services.recovery import evaluate_case
            decision = evaluate_case(session, case, booking, inventory=[incompatible])
            assert decision.decision_code == "manual_review"
            assert "alternative_route_incompatible" in decision.evidence["rejected_candidate_reasons"]
    finally:
        teardown_case(client)


def test_valid_synthetic_alternative_is_recommended_for_review(tmp_path):
    client, _, case_id = setup_case(tmp_path, "BK-101")
    try:
        response = client.post("/cases/{}/evaluate".format(case_id))
        assert response.status_code == 200
        body = response.json()
        assert body["decision_code"] == "recommend_for_review"
        assert body["candidates"]
        assert body["candidates"][0]["simulated"] is True
        assert body["simulated_inventory"] is True
        assert body["requires_human_approval"] is True
    finally:
        teardown_case(client)


def test_no_eligible_alternatives_results_in_manual_review(tmp_path):
    client, app, case_id = setup_case(tmp_path, "BK-101")
    try:
        from app.services.recovery import evaluate_case
        with app.state.session_factory.begin() as session:
            case = session.get(DisruptionCase, case_id)
            booking = session.get(Booking, case.booking_id)
            decision = evaluate_case(session, case, booking, inventory=[])
            assert decision.decision_code == "manual_review"
            assert decision.reason_codes == ["no_eligible_simulated_alternative"]
    finally:
        teardown_case(client)


def test_risky_or_incomplete_itinerary_escalates(tmp_path):
    client, app, case_id = setup_case(tmp_path)
    try:
        with app.state.session_factory.begin() as session:
            case = session.get(DisruptionCase, case_id)
            booking = session.get(Booking, case.booking_id)
            itinerary = list(booking.itinerary)
            itinerary[1] = dict(itinerary[1], scheduled_departure="2026-10-28T10:45:00-07:00")
            booking.itinerary = itinerary
        response = client.post("/cases/{}/evaluate".format(case_id))
        body = response.json()
        assert response.status_code == 200
        assert body["decision_code"] == "escalate"
        assert "itinerary_connection_requires_review" in body["reason_codes"]
        assert body["candidates"] == []
    finally:
        teardown_case(client)


def test_case_list_and_detail_include_ordered_itinerary(tmp_path):
    client, _, case_id = setup_case(tmp_path)
    try:
        listing = client.get("/cases")
        detail = client.get("/cases/{}".format(case_id))
        assert listing.status_code == 200
        assert len(listing.json()) == 2
        assert detail.status_code == 200
        assert detail.json()["booking_ref"] == "BK-100"
        assert [leg["sequence"] for leg in detail.json()["itinerary"]] == [1, 2]
        assert detail.json()["latest_decision"] is None
    finally:
        teardown_case(client)


def test_evaluation_contains_reason_policy_version_and_audit_evidence(tmp_path):
    client, app, case_id = setup_case(tmp_path, "BK-101")
    try:
        response = client.post("/cases/{}/evaluate".format(case_id))
        body = response.json()
        assert body["reason_codes"]
        assert body["policy_id"] == DEFAULT_POLICY.policy_id
        assert body["policy_version"] == DEFAULT_POLICY.version
        with app.state.session_factory() as session:
            decision = session.get(RecoveryDecision, body["decision_id"])
            audit = session.scalar(select(AuditEvent).where(AuditEvent.action == "case_evaluated"))
            assert decision.evidence["inventory_is_simulated"] is True
            assert audit.details["policy_version"] == body["policy_version"]
    finally:
        teardown_case(client)


def test_case_detail_returns_latest_persisted_decision(tmp_path):
    client, _, case_id = setup_case(tmp_path)
    try:
        evaluated = client.post("/cases/{}/evaluate".format(case_id))
        assert evaluated.status_code == 200
        response = client.get("/cases/{}".format(case_id))
        assert response.status_code == 200
        persisted = response.json()["latest_decision"]
        assert persisted["decision_id"] == evaluated.json()["decision_id"]
        assert persisted["decision_code"] == evaluated.json()["decision_code"]
        assert persisted["policy_version"] == evaluated.json()["policy_version"]
    finally:
        teardown_case(client)


def test_evaluation_does_not_execute_simulated_side_effects(tmp_path):
    client, app, case_id = setup_case(tmp_path, "BK-101")
    try:
        with app.state.session_factory() as session:
            before_events = session.scalar(select(func.count()).select_from(DisruptionEvent))
            before_cases = session.scalar(select(func.count()).select_from(DisruptionCase))
        response = client.post("/cases/{}/evaluate".format(case_id))
        assert response.status_code == 200
        assert response.json()["side_effects_executed"] is False
        with app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(DisruptionEvent)) == before_events
            assert session.scalar(select(func.count()).select_from(DisruptionCase)) == before_cases
            assert session.scalar(select(AuditEvent.action).where(AuditEvent.action == "case_evaluated"))
    finally:
        teardown_case(client)
