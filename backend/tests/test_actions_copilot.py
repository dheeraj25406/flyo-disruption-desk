from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import create_app
from app.models import AuditEvent, Booking, DisruptionCase, RecoveryAction, RecoveryDecision
from conftest import seed_legacy_test_data


def setup_recommended_case(tmp_path, booking_ref="BK-101"):
    app = create_app("sqlite:///{}".format(tmp_path / "actions.db"), seed_data=False)
    client = TestClient(app)
    client.__enter__()
    seed_legacy_test_data(app.state.session_factory)
    event_response = client.post("/webhooks/flight-alerts", json={
        "event_id": "evt-actions", "fid": "FL-1001", "changes": {"status": "cancel"}
    })
    assert event_response.status_code == 200
    with app.state.session_factory() as session:
        case = session.scalar(select(DisruptionCase).join(Booking).where(Booking.booking_ref == booking_ref))
        case_id = case.id
    evaluation = client.post("/cases/{}/evaluate".format(case_id))
    assert evaluation.status_code == 200
    return client, app, case_id, evaluation.json()


def close_client(client):
    client.__exit__(None, None, None)


def propose_rebook(client, case_id, decision_id, key="idem-rebook"):
    return client.post("/cases/{}/actions".format(case_id), json={
        "action_type": "REBOOK",
        "idempotency_key": key,
        "decision_id": decision_id,
        "inventory_id": "SIM-001",
    })


def test_eligible_recommendation_creates_pending_action(tmp_path):
    client, _, case_id, evaluation = setup_recommended_case(tmp_path)
    try:
        response = propose_rebook(client, case_id, evaluation["decision_id"])
        assert response.status_code == 201
        action = response.json()
        assert action["state"] == "PENDING_APPROVAL"
        assert action["action_type"] == "REBOOK"
        assert action["proposed_payload"]["alternative"]["simulated"] is True
        inspected = client.get("/actions/{}".format(action["action_id"]))
        assert inspected.status_code == 200
        assert inspected.json()["state"] == "PENDING_APPROVAL"
    finally:
        close_client(client)


def test_missing_or_ineligible_recommendation_cannot_create_rebook(tmp_path):
    app = create_app("sqlite:///{}".format(tmp_path / "ineligible.db"), seed_data=False)
    client = TestClient(app)
    client.__enter__()
    seed_legacy_test_data(app.state.session_factory)
    try:
        client.post("/webhooks/flight-alerts", json={
            "event_id": "evt-ineligible", "fid": "FL-1001", "changes": {"status": "cancel"}
        })
        with app.state.session_factory() as session:
            case_id = session.scalar(select(DisruptionCase.id).order_by(DisruptionCase.id))
        missing = client.post("/cases/{}/actions".format(case_id), json={
            "action_type": "REBOOK", "idempotency_key": "missing-decision", "inventory_id": "SIM-001"
        })
        assert missing.status_code == 409

        evaluation = client.post("/cases/{}/evaluate".format(case_id)).json()
        with app.state.session_factory.begin() as session:
            decision = session.get(RecoveryDecision, evaluation["decision_id"])
            decision.decision_code = "manual_review"
            decision.candidates = []
        ineligible = propose_rebook(client, case_id, evaluation["decision_id"], "no-recommendation")
        assert ineligible.status_code == 409
        assert ineligible.json()["detail"]

        refund_proposal = client.post("/cases/{}/actions".format(case_id), json={
            "action_type": "REFUND_REVIEW",
            "idempotency_key": "refund-review",
            "decision_id": evaluation["decision_id"],
        })
        assert refund_proposal.status_code == 201
        refund_result = client.post(
            "/actions/{}/approve".format(refund_proposal.json()["action_id"]), json={"approved": True}
        )
        assert refund_result.status_code == 200
        assert refund_result.json()["execution_result"]["money_refunded"] is False
    finally:
        close_client(client)


def test_approval_executes_simulation_once_and_replay_is_rejected(tmp_path):
    client, app, case_id, evaluation = setup_recommended_case(tmp_path)
    try:
        action = propose_rebook(client, case_id, evaluation["decision_id"]).json()
        approved = client.post("/actions/{}/approve".format(action["action_id"]), json={
            "approved": True, "approved_by": "test-operator"
        })
        assert approved.status_code == 200
        result = approved.json()
        assert result["state"] == "SUCCEEDED"
        assert result["execution_result"]["simulated"] is True
        assert result["execution_result"]["real_world_completed"] is False
        assert result["execution_result"]["outcome"] == "simulated_rebooking_recorded"

        duplicate = client.post("/actions/{}/approve".format(action["action_id"]), json={"approved": True})
        assert duplicate.status_code == 409
        with app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(RecoveryAction)) == 1
            assert session.scalar(select(func.count()).select_from(AuditEvent).where(
                AuditEvent.action == "action_execution_started"
            )) == 1
    finally:
        close_client(client)


def test_rejected_action_cannot_execute_and_invalid_transition_is_audited(tmp_path):
    client, app, case_id, evaluation = setup_recommended_case(tmp_path)
    try:
        action = propose_rebook(client, case_id, evaluation["decision_id"]).json()
        rejected = client.post("/actions/{}/reject".format(action["action_id"]), json={"reason": "operator chose review"})
        assert rejected.status_code == 200
        assert rejected.json()["state"] == "REJECTED"
        attempted = client.post("/actions/{}/approve".format(action["action_id"]), json={"approved": True})
        assert attempted.status_code == 409
        duplicate_reject = client.post("/actions/{}/reject".format(action["action_id"]), json={})
        assert duplicate_reject.status_code == 409
        with app.state.session_factory() as session:
            stored = session.get(RecoveryAction, action["action_id"])
            assert stored.state == "REJECTED"
            assert stored.execution_result is None
            assert session.scalar(select(AuditEvent.action).where(
                AuditEvent.action == "action_transition_rejected"
            ))
    finally:
        close_client(client)


def test_missing_case_and_action_return_404(tmp_path):
    client, _, _, _ = setup_recommended_case(tmp_path)
    try:
        assert client.get("/cases/999999").status_code == 404
        assert client.post("/cases/999999/actions", json={
            "action_type": "REBOOK", "idempotency_key": "no-case", "inventory_id": "SIM-001"
        }).status_code == 404
        assert client.get("/actions/not-a-real-action").status_code == 404
        assert client.post("/actions/not-a-real-action/approve", json={"approved": True}).status_code == 404
        assert client.post("/actions/not-a-real-action/reject", json={}).status_code == 404
    finally:
        close_client(client)


def test_simulated_failure_is_persisted_as_failed(tmp_path):
    client, app, case_id, evaluation = setup_recommended_case(tmp_path)
    try:
        action = propose_rebook(client, case_id, evaluation["decision_id"]).json()
        with app.state.session_factory.begin() as session:
            stored = session.get(RecoveryAction, action["action_id"])
            stored.proposed_payload = dict(stored.proposed_payload, _simulate_failure=True)
        response = client.post("/actions/{}/approve".format(action["action_id"]), json={"approved": True})
        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "FAILED"
        assert body["failure_reason"] == "SIMULATED_DOWNSTREAM_FAILURE"
        assert body["execution_result"]["real_world_completed"] is False
        with app.state.session_factory() as session:
            assert session.get(RecoveryAction, action["action_id"]).state == "FAILED"
    finally:
        close_client(client)


def test_case_audit_timeline_contains_proposal_approval_and_execution(tmp_path):
    client, _, case_id, evaluation = setup_recommended_case(tmp_path)
    try:
        action = propose_rebook(client, case_id, evaluation["decision_id"]).json()
        client.post("/actions/{}/approve".format(action["action_id"]), json={"approved": True})
        client.post("/cases/{}/draft-message".format(case_id))
        timeline = client.get("/cases/{}".format(case_id)).json()["audit_timeline"]
        action_names = [entry["action"] for entry in timeline]
        assert action_names[0] == "flight_event_received"
        assert "flight_event_processed" in action_names
        assert "case_created" in action_names
        assert "case_evaluated" in action_names
        assert "action_proposed" in action_names
        assert "action_approved" in action_names
        assert "action_execution_started" in action_names
        assert "action_succeeded" in action_names
        assert "message_draft_generated" in action_names
    finally:
        close_client(client)


def test_fallback_draft_is_grounded_and_never_sends_message(tmp_path):
    client, app, case_id, evaluation = setup_recommended_case(tmp_path)
    try:
        response = client.post("/cases/{}/draft-message".format(case_id))
        assert response.status_code == 200
        body = response.json()
        assert body["provider"] == "deterministic_fallback"
        assert body["draft_only"] is True
        assert body["sent"] is False
        assert "BK-101" in body["case_summary"]
        assert "FL-1001" in body["case_summary"]
        assert body["recommendation"]["decision_id"] == evaluation["decision_id"]
        assert "not been sent" in body["draft_message"]
        with app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(RecoveryAction)) == 0
            draft_audit = session.scalar(select(AuditEvent).where(
                AuditEvent.action == "message_draft_generated"
            ))
            assert draft_audit.details["sent"] is False
        action = client.post("/cases/{}/actions".format(case_id), json={
            "action_type": "SEND_MESSAGE",
            "idempotency_key": "send-draft-after-approval",
            "decision_id": evaluation["decision_id"],
            "message": body["draft_message"],
        })
        assert action.status_code == 201
        assert action.json()["state"] == "PENDING_APPROVAL"
        sent = client.post("/actions/{}/approve".format(action.json()["action_id"]), json={"approved": True})
        assert sent.status_code == 200
        assert sent.json()["execution_result"]["real_message_sent"] is False
    finally:
        close_client(client)


def test_evaluation_alone_does_not_create_or_execute_action(tmp_path):
    client, app, _, _ = setup_recommended_case(tmp_path)
    try:
        with app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(RecoveryAction)) == 0
            assert session.scalar(select(func.count()).select_from(AuditEvent).where(
                AuditEvent.action.like("action_%")
            )) == 0
    finally:
        close_client(client)


def test_duplicate_idempotency_key_returns_same_proposal_without_duplicate(tmp_path):
    client, app, case_id, evaluation = setup_recommended_case(tmp_path)
    try:
        first = propose_rebook(client, case_id, evaluation["decision_id"], "same-key")
        replay = propose_rebook(client, case_id, evaluation["decision_id"], "same-key")
        assert first.status_code == 201
        assert replay.status_code == 200
        assert first.json()["action_id"] == replay.json()["action_id"]
        with app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(RecoveryAction)) == 1
    finally:
        close_client(client)


def test_conflicting_duplicate_idempotency_key_is_rejected(tmp_path):
    client, _, case_id, evaluation = setup_recommended_case(tmp_path)
    try:
        first = propose_rebook(client, case_id, evaluation["decision_id"], "same-key")
        conflict = client.post("/cases/{}/actions".format(case_id), json={
            "action_type": "REBOOK",
            "idempotency_key": "same-key",
            "decision_id": evaluation["decision_id"],
            "inventory_id": "SIM-002",
        })
        assert first.status_code == 201
        assert conflict.status_code == 409
    finally:
        close_client(client)
