"""Deterministic end-to-end scenarios for the synthetic disruption workflow."""

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import create_app
from app.models import AuditEvent, Booking, DisruptionCase, DisruptionEvent, RecoveryAction, RecoveryDecision
from app.services.policy import Alternative
from app.services.recovery import evaluate_case


@contextmanager
def isolated_client(tmp_path, name="scenario"):
    app = create_app("sqlite:///{}".format(tmp_path / (name + ".db")))
    with TestClient(app) as client:
        yield app, client


def cancel(client, event_id="evt-scenario", flight="FL-1001"):
    return client.post("/webhooks/flight-alerts", json={
        "event_id": event_id, "fid": flight, "changes": {"status": "cancel"}
    })


def get_case_id(client, booking_ref="BK-100"):
    rows = client.get("/cases").json()
    return next(item["id"] for item in rows if item["booking_ref"] == booking_ref)


def setup_case(tmp_path, booking_ref="BK-101", name="case"):
    app = create_app("sqlite:///{}".format(tmp_path / (name + ".db")))
    client = TestClient(app)
    client.__enter__()
    assert cancel(client, "evt-" + name).status_code == 200
    case_id = get_case_id(client, booking_ref)
    return app, client, case_id


def evaluate(client, case_id):
    response = client.post("/cases/{}/evaluate".format(case_id))
    assert response.status_code == 200, response.text
    return response.json()


def propose(client, case_id, decision_id, key="scenario-key", candidate="SIM-001"):
    return client.post("/cases/{}/actions".format(case_id), json={
        "action_type": "REBOOK", "idempotency_key": key,
        "decision_id": decision_id, "inventory_id": candidate,
    })


def expect(actual, expected):
    assert actual == expected, "EXPECTED: {!r}\nACTUAL:   {!r}".format(expected, actual)


# Webhook scenarios: cancellation targeting, idempotency, and safe schema rejection.
def test_webhook__cancellation_creates_two_affected_booking_cases(tmp_path):
    with isolated_client(tmp_path) as (app, client):
        response = cancel(client)
        expect((response.status_code, response.json()["cases_created"]), (200, 2))
        with app.state.session_factory() as session:
            expect(session.scalar(select(func.count()).select_from(DisruptionCase)), 2)


def test_webhook__unrelated_booking_receives_no_case(tmp_path):
    with isolated_client(tmp_path) as (_, client):
        cancel(client)
        refs = {row["booking_ref"] for row in client.get("/cases").json()}
        expect("BK-102" in refs, False)


def test_webhook__duplicate_delivery_does_not_duplicate_event_or_cases(tmp_path):
    with isolated_client(tmp_path) as (app, client):
        cancel(client, "evt-replay")
        replay = cancel(client, "evt-replay")
        expect((replay.json()["status"], replay.json()["cases_created"]), ("already_processed", 0))
        with app.state.session_factory() as session:
            expect(session.scalar(select(func.count()).select_from(DisruptionEvent)), 1)
            expect(session.scalar(select(func.count()).select_from(DisruptionCase)), 2)


def test_webhook__same_identifier_with_conflicting_payload_stays_idempotent(tmp_path):
    with isolated_client(tmp_path) as (app, client):
        cancel(client, "evt-conflict", "FL-1001")
        response = cancel(client, "evt-conflict", "FL-2002")
        expect((response.json()["duplicate"], response.json()["cases_created"]), (True, 0))
        with app.state.session_factory() as session:
            event = session.scalar(select(DisruptionEvent))
            expect(event.fid, "FL-1001")
            expect(session.scalar(select(func.count()).select_from(DisruptionEvent)), 1)


def test_webhook__unknown_flight_returns_404_without_persisting_event(tmp_path):
    with isolated_client(tmp_path) as (app, client):
        response = cancel(client, "evt-unknown", "FL-NOT-FOUND")
        expect(response.status_code, 404)
        with app.state.session_factory() as session:
            expect(session.scalar(select(func.count()).select_from(DisruptionEvent)), 0)


def test_webhook__unsupported_status_is_rejected(tmp_path):
    with isolated_client(tmp_path) as (app, client):
        response = client.post("/webhooks/flight-alerts", json={
            "event_id": "evt-delay", "fid": "FL-1001", "changes": {"status": "delayed"}
        })
        expect(response.status_code, 422)
        with app.state.session_factory() as session:
            expect(session.scalar(select(func.count()).select_from(DisruptionEvent)), 0)


def test_webhook__partial_update_is_rejected_without_status(tmp_path):
    with isolated_client(tmp_path) as (_, client):
        response = client.post("/webhooks/flight-alerts", json={
            "event_id": "evt-partial", "fid": "FL-1001", "changes": {"gate": "A2"}
        })
        expect(response.status_code, 422)


def test_webhook__event_ordering_metadata_is_not_supported_and_rejected(tmp_path):
    with isolated_client(tmp_path) as (_, client):
        response = client.post("/webhooks/flight-alerts", json={
            "event_id": "evt-ordered", "fid": "FL-1001", "occurred_at": "2026-10-27T12:00:00Z",
            "changes": {"status": "cancel"},
        })
        expect(response.status_code, 422)


# Risk and deterministic policy scenarios.
def test_risk__safe_connection_is_screened_feasible(tmp_path):
    with isolated_client(tmp_path) as (_, client):
        cancel(client)
        case_id = get_case_id(client, "BK-100")
        result = evaluate(client, case_id)
        expect(result["connection_assessments"][0]["status"], "feasible")


def test_risk__connection_below_minimum_escalates(tmp_path):
    app, client, case_id = setup_case(tmp_path, "BK-100")
    try:
        with app.state.session_factory.begin() as session:
            booking = session.scalar(select(Booking).where(Booking.booking_ref == "BK-100"))
            booking.itinerary = [dict(booking.itinerary[0]), dict(booking.itinerary[1], scheduled_departure="2026-10-28T11:20:00-07:00")]
        result = evaluate(client, case_id)
        expect((result["decision_code"], result["connection_assessments"][0]["status"]), ("escalate", "insufficient"))
    finally:
        client.__exit__(None, None, None)


def test_risk__impossible_connection_escalates(tmp_path):
    app, client, case_id = setup_case(tmp_path, "BK-100")
    try:
        with app.state.session_factory.begin() as session:
            booking = session.scalar(select(Booking).where(Booking.booking_ref == "BK-100"))
            booking.itinerary = [dict(booking.itinerary[0]), dict(booking.itinerary[1], scheduled_departure="2026-10-28T10:00:00-07:00")]
        result = evaluate(client, case_id)
        expect((result["decision_code"], result["connection_assessments"][0]["status"]), ("escalate", "impossible"))
    finally:
        client.__exit__(None, None, None)


def test_risk__missing_schedule_escalates(tmp_path):
    app, client, case_id = setup_case(tmp_path, "BK-101")
    try:
        with app.state.session_factory.begin() as session:
            booking = session.scalar(select(Booking).where(Booking.booking_ref == "BK-101"))
            booking.itinerary = [dict(booking.itinerary[0], scheduled_arrival=None)]
        result = evaluate(client, case_id)
        expect((result["decision_code"], "disrupted_leg_schedule_or_timezone_missing" in result["reason_codes"]), ("escalate", True))
    finally:
        client.__exit__(None, None, None)


def test_risk__invalid_timezone_escalates(tmp_path):
    app, client, case_id = setup_case(tmp_path, "BK-101")
    try:
        with app.state.session_factory.begin() as session:
            booking = session.scalar(select(Booking).where(Booking.booking_ref == "BK-101"))
            booking.itinerary = [dict(booking.itinerary[0], arrival_timezone="Invalid/Zone")]
        result = evaluate(client, case_id)
        expect(result["decision_code"], "escalate")
    finally:
        client.__exit__(None, None, None)


def test_risk__route_mismatch_escalates(tmp_path):
    app, client, case_id = setup_case(tmp_path, "BK-100")
    try:
        with app.state.session_factory.begin() as session:
            booking = session.scalar(select(Booking).where(Booking.booking_ref == "BK-100"))
            booking.itinerary = [dict(booking.itinerary[0]), dict(booking.itinerary[1], origin="SEA")]
        result = evaluate(client, case_id)
        expect(result["connection_assessments"][0]["status"], "route_mismatch")
    finally:
        client.__exit__(None, None, None)


def test_policy__eligible_simulated_alternative_recommends_for_review(tmp_path):
    app, client, case_id = setup_case(tmp_path)
    try:
        result = evaluate(client, case_id)
        expect((result["decision_code"], bool(result["candidates"]), result["requires_human_approval"]),
               ("recommend_for_review", True, True))
    finally:
        client.__exit__(None, None, None)


def test_policy__incompatible_alternative_route_is_rejected(tmp_path):
    app, client, case_id = setup_case(tmp_path)
    try:
        alternative = Alternative("ONLY-WRONG", "SIM-WRONG", "SJC", "LAX", "2026-10-28T09:00:00-07:00",
                                  "2026-10-28T10:00:00-07:00", "America/Los_Angeles", "America/Los_Angeles", True)
        with app.state.session_factory.begin() as session:
            case = session.get(DisruptionCase, case_id)
            booking = session.scalar(select(Booking).where(Booking.booking_ref == "BK-101"))
            decision = evaluate_case(session, case, booking, inventory=[alternative])
            expect((decision.decision_code, decision.evidence["rejected_candidate_reasons"]),
                   ("manual_review", ["alternative_route_incompatible"]))
    finally:
        client.__exit__(None, None, None)


def test_policy__passenger_deadline_rejects_all_late_alternatives(tmp_path):
    app, client, case_id = setup_case(tmp_path)
    try:
        with app.state.session_factory.begin() as session:
            booking = session.scalar(select(Booking).where(Booking.booking_ref == "BK-101"))
            booking.passenger_constraints = {"must_arrive_by": "2026-10-28T08:00:00-07:00"}
        result = evaluate(client, case_id)
        expect((result["decision_code"], result["candidates"]), ("manual_review", []))
    finally:
        client.__exit__(None, None, None)


def test_policy__reevaluation_persists_new_decision_and_latest_evidence(tmp_path):
    app, client, case_id = setup_case(tmp_path)
    try:
        first, second = evaluate(client, case_id), evaluate(client, case_id)
        expect(second["decision_id"] > first["decision_id"], True)
        detail = client.get("/cases/{}".format(case_id)).json()
        expect(detail["latest_decision"]["decision_id"], second["decision_id"])
        with app.state.session_factory() as session:
            expect(session.scalar(select(func.count()).select_from(RecoveryDecision).where(RecoveryDecision.case_id == case_id)), 2)
            expect(session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "case_evaluated")), 2)
    finally:
        client.__exit__(None, None, None)


# Human approval, execution, idempotency, draft, and audit safety scenarios.
def test_action__evaluation_alone_never_creates_an_action(tmp_path):
    app, client, case_id = setup_case(tmp_path)
    try:
        evaluate(client, case_id)
        with app.state.session_factory() as session:
            expect(session.scalar(select(func.count()).select_from(RecoveryAction)), 0)
    finally:
        client.__exit__(None, None, None)


def test_action__proposal_waits_for_approval(tmp_path):
    app, client, case_id = setup_case(tmp_path)
    try:
        decision = evaluate(client, case_id)
        response = propose(client, case_id, decision["decision_id"])
        expect((response.status_code, response.json()["state"]), (201, "PENDING_APPROVAL"))
        with app.state.session_factory() as session:
            expect(session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "action_execution_started")), 0)
    finally:
        client.__exit__(None, None, None)


def test_action__approval_executes_only_simulated_outcome(tmp_path):
    app, client, case_id = setup_case(tmp_path)
    try:
        decision = evaluate(client, case_id)
        action = propose(client, case_id, decision["decision_id"]).json()
        result = client.post("/actions/{}/approve".format(action["action_id"]), json={"approved": True}).json()
        expect((result["state"], result["execution_result"]["simulated"], result["execution_result"]["real_world_completed"]),
               ("SUCCEEDED", True, False))
    finally:
        client.__exit__(None, None, None)


def test_action__repeated_approval_cannot_execute_twice(tmp_path):
    app, client, case_id = setup_case(tmp_path)
    try:
        decision = evaluate(client, case_id)
        action = propose(client, case_id, decision["decision_id"]).json()
        url = "/actions/{}/approve".format(action["action_id"])
        expect(client.post(url, json={"approved": True}).status_code, 200)
        expect(client.post(url, json={"approved": True}).status_code, 409)
        with app.state.session_factory() as session:
            expect(session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "action_execution_started")), 1)
    finally:
        client.__exit__(None, None, None)


def test_action__rejected_action_never_executes(tmp_path):
    app, client, case_id = setup_case(tmp_path)
    try:
        decision = evaluate(client, case_id)
        action = propose(client, case_id, decision["decision_id"]).json()
        expect(client.post("/actions/{}/reject".format(action["action_id"]), json={"reason": "declined"}).json()["state"], "REJECTED")
        expect(client.post("/actions/{}/approve".format(action["action_id"]), json={"approved": True}).status_code, 409)
        with app.state.session_factory() as session:
            stored = session.get(RecoveryAction, action["action_id"])
            expect((stored.state, stored.execution_result), ("REJECTED", None))
            expect(session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "action_execution_started")), 0)
    finally:
        client.__exit__(None, None, None)


def test_action__same_idempotency_key_returns_same_pending_action(tmp_path):
    app, client, case_id = setup_case(tmp_path)
    try:
        decision = evaluate(client, case_id)
        first = propose(client, case_id, decision["decision_id"], "same-key")
        again = propose(client, case_id, decision["decision_id"], "same-key")
        expect((first.status_code, again.status_code, first.json()["action_id"] == again.json()["action_id"]), (201, 200, True))
    finally:
        client.__exit__(None, None, None)


def test_action__conflicting_idempotency_request_returns_409(tmp_path):
    _, client, case_id = setup_case(tmp_path)
    try:
        decision = evaluate(client, case_id)
        propose(client, case_id, decision["decision_id"], "same-key", "SIM-001")
        conflict = propose(client, case_id, decision["decision_id"], "same-key", "SIM-002")
        expect(conflict.status_code, 409)
    finally:
        client.__exit__(None, None, None)


def test_action__missing_decision_evidence_blocks_proposal(tmp_path):
    _, client, case_id = setup_case(tmp_path)
    try:
        response = propose(client, case_id, 999999)
        expect(response.status_code, 409)
    finally:
        client.__exit__(None, None, None)


def test_action__decision_from_another_case_is_rejected(tmp_path):
    _, client, _ = setup_case(tmp_path, "BK-101", "cross-case")
    try:
        cases = client.get("/cases").json()
        case_a = next(row["id"] for row in cases if row["booking_ref"] == "BK-100")
        case_b = next(row["id"] for row in cases if row["booking_ref"] == "BK-101")
        decision_b = evaluate(client, case_b)
        expect(propose(client, case_a, decision_b["decision_id"], "cross-case-key").status_code, 409)
    finally:
        client.__exit__(None, None, None)


def test_action__candidate_missing_from_saved_decision_is_rejected(tmp_path):
    _, client, case_id = setup_case(tmp_path)
    try:
        decision = evaluate(client, case_id)
        expect(propose(client, case_id, decision["decision_id"], "not-in-evidence", "SIM-NOT-THERE").status_code, 409)
    finally:
        client.__exit__(None, None, None)


def test_action__simulated_downstream_failure_remains_failed(tmp_path):
    app, client, case_id = setup_case(tmp_path)
    try:
        decision = evaluate(client, case_id)
        action = propose(client, case_id, decision["decision_id"]).json()
        with app.state.session_factory.begin() as session:
            stored = session.get(RecoveryAction, action["action_id"])
            stored.proposed_payload = dict(stored.proposed_payload, _simulate_failure=True)
        result = client.post("/actions/{}/approve".format(action["action_id"]), json={"approved": True}).json()
        expect((result["state"], result["failure_reason"], result["execution_result"]["real_world_completed"]),
               ("FAILED", "SIMULATED_DOWNSTREAM_FAILURE", False))
        expect(result["state"] == "SUCCEEDED", False)
    finally:
        client.__exit__(None, None, None)


def test_action__draft_generation_is_unsent_and_creates_no_action(tmp_path):
    app, client, case_id = setup_case(tmp_path)
    try:
        evaluate(client, case_id)
        draft = client.post("/cases/{}/draft-message".format(case_id)).json()
        expect((draft["draft_only"], draft["sent"], draft["provider"]), (True, False, "deterministic_fallback"))
        with app.state.session_factory() as session:
            expect(session.scalar(select(func.count()).select_from(RecoveryAction)), 0)
            audit = session.scalar(select(AuditEvent).where(AuditEvent.action == "message_draft_generated"))
            expect(audit.details["sent"], False)
    finally:
        client.__exit__(None, None, None)


def test_audit__timeline_distinguishes_proposal_approval_execution_and_result(tmp_path):
    _, client, case_id = setup_case(tmp_path)
    try:
        decision = evaluate(client, case_id)
        action = propose(client, case_id, decision["decision_id"]).json()
        client.post("/actions/{}/approve".format(action["action_id"]), json={"approved": True})
        names = [item["action"] for item in client.get("/cases/{}".format(case_id)).json()["audit_timeline"]]
        expected = ["action_proposed", "action_approved", "action_execution_started", "action_succeeded"]
        expect([name for name in names if name in expected], expected)
    finally:
        client.__exit__(None, None, None)


def test_audit__invalid_transition_is_recorded_without_execution(tmp_path):
    app, client, case_id = setup_case(tmp_path)
    try:
        decision = evaluate(client, case_id)
        action = propose(client, case_id, decision["decision_id"]).json()
        client.post("/actions/{}/reject".format(action["action_id"]), json={})
        response = client.post("/actions/{}/approve".format(action["action_id"]), json={"approved": True})
        expect(response.status_code, 409)
        with app.state.session_factory() as session:
            expect(session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "action_transition_rejected")), 1)
            expect(session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "action_execution_started")), 0)
    finally:
        client.__exit__(None, None, None)
