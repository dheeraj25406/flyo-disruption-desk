from google.genai import types
from sqlalchemy import func, select

from app.main import create_app
from app.models import AuditEvent, RecoveryAction
from app.services.copilot import DeterministicCopilot, GeminiCopilot, REQUIRED_MODEL


class FakeAgent(GeminiCopilot):
    def __init__(self, turns):
        super().__init__("test-key", model=REQUIRED_MODEL)
        self.turns = list(turns)
        self.turn_calls = []

    def agent_turn(self, contents):
        self.turn_calls.append(contents)
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        calls = [
            {"name": name, "arguments": args}
            for name, args in turn.get("calls", [])
        ]
        parts = [types.Part(function_call=types.FunctionCall(name=call["name"], args=call["arguments"])) for call in calls]
        content = types.Content(role="model", parts=parts) if calls else types.Content(role="model", parts=[types.Part.from_text(text=turn.get("text", ""))])
        return {"calls": calls, "content": content, "text": turn.get("text")}


class QuotaError(Exception):
    status_code = 429


def invoke_agent(tmp_path, provider, allow_proposal=False):
    app = create_app("sqlite:///{}".format(tmp_path / "agent.db"), copilot_provider=provider)
    from fastapi.testclient import TestClient
    client = TestClient(app)
    client.__enter__()
    event = client.post("/webhooks/flight-alerts", json={
        "event_id": "evt-agent-test", "fid": "FL-1001", "changes": {"status": "cancel"}
    })
    assert event.status_code == 200
    case_id = next(item["id"] for item in client.get("/cases").json() if item["booking_ref"] == "BK-101")
    response = client.post("/cases/{}/agent/run".format(case_id), json={
        "objective": "Review the disruption and available synthetic recovery options.",
        "allow_proposal": allow_proposal,
    })
    return app, client, case_id, response


def close(client):
    client.__exit__(None, None, None)


def test_agent_uses_backend_tools_and_creates_only_pending_proposal(tmp_path):
    provider = FakeAgent([
        {"calls": [
            ("search_synthetic_alternatives", {}),
            ("check_connection_feasibility", {}),
        ]},
        {"calls": [("compare_recovery_options", {"alternative_ids": ["SIM-001"]})]},
        {"calls": [("propose_recovery_action", {"inventory_id": "SIM-001"})]},
        {"text": "The simulated option passed policy screening. A proposal is pending human approval."},
    ])
    app, client, case_id, response = invoke_agent(tmp_path, provider, allow_proposal=True)
    try:
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["metadata"]["provider"] == "gemini"
        assert body["metadata"]["model"] == REQUIRED_MODEL
        assert [item["name"] for item in body["tool_results"]] == [
            "search_synthetic_alternatives", "check_connection_feasibility",
            "compare_recovery_options", "propose_recovery_action",
        ]
        assert body["tool_results"][0]["result"]["inventory_is_simulated"] is True
        assert body["tool_results"][2]["result"]["options"][0]["inventory_id"] == "SIM-001"
        assert body["proposed_action_id"]
        assert body["approval_required"] is True
        with app.state.session_factory() as session:
            action = session.get(RecoveryAction, body["proposed_action_id"])
            assert action.state == "PENDING_APPROVAL"
            assert action.executed_at is None
            assert action.execution_result is None
            names = list(session.scalars(select(AuditEvent.action).where(AuditEvent.action.like("agent_%"))))
            assert names.count("agent_tool_called") == 4
            assert "agent_run_completed" in names
    finally:
        close(client)


def test_unknown_tool_name_uses_fallback_without_dispatch(tmp_path):
    provider = FakeAgent([{"calls": [("execute_sql", {"query": "delete"})]}])
    app, client, _, response = invoke_agent(tmp_path, provider)
    try:
        assert response.status_code == 200
        assert response.json()["metadata"]["validation_status"] == "fallback_invalid_tool_call"
        assert response.json()["metadata"]["fallback_used"] is True
        assert response.json()["tool_results"] == []
        with app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(RecoveryAction)) == 0
            assert session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "agent_tool_rejected")) == 1
    finally:
        close(client)


def test_invalid_tool_arguments_use_fallback(tmp_path):
    provider = FakeAgent([{"calls": [("search_synthetic_alternatives", {"fid": "FL-9999"})]}])
    _, client, _, response = invoke_agent(tmp_path, provider)
    try:
        assert response.status_code == 200
        assert response.json()["metadata"]["validation_status"] == "fallback_invalid_tool_call"
        assert response.json()["tool_results"] == []
    finally:
        close(client)


def test_compare_tool_rejects_model_invented_alternative_id(tmp_path):
    provider = FakeAgent([{"calls": [("compare_recovery_options", {"alternative_ids": ["SIM-FABRICATED"]})]}])
    _, client, _, response = invoke_agent(tmp_path, provider)
    try:
        assert response.status_code == 200
        assert response.json()["metadata"]["validation_status"] == "fallback_invalid_tool_call"
        assert response.json()["tool_results"] == []
    finally:
        close(client)


def test_agent_tool_call_and_iteration_limits_are_enforced(tmp_path):
    repeated = [{"calls": [("check_connection_feasibility", {})]} for _ in range(4)]
    _, client, _, response = invoke_agent(tmp_path, FakeAgent(repeated))
    try:
        assert response.status_code == 200
        body = response.json()
        assert body["metadata"]["validation_status"] == "fallback_iteration_limit"
        assert len(body["tool_results"]) == 4
        assert len(body["tool_results"]) <= 4
    finally:
        close(client)


def test_no_eligible_alternatives_are_reported_as_manual_review(tmp_path):
    provider = FakeAgent([
        {"calls": [("search_synthetic_alternatives", {})]},
        {"text": "No eligible simulated options were returned."},
    ])
    app = create_app("sqlite:///{}".format(tmp_path / "agent.db"), copilot_provider=provider)
    from fastapi.testclient import TestClient
    client = TestClient(app)
    client.__enter__()
    try:
        event = client.post("/webhooks/flight-alerts", json={
            "event_id": "evt-agent-empty", "fid": "FL-1001", "changes": {"status": "cancel"}
        })
        assert event.status_code == 200
        case_id = next(item["id"] for item in client.get("/cases").json() if item["booking_ref"] == "BK-101")
        with app.state.session_factory.begin() as session:
            from app.models import Booking, DisruptionCase
            case = session.get(DisruptionCase, case_id)
            booking = session.get(Booking, case.booking_id)
            booking.passenger_constraints = {"must_arrive_by": "2026-10-28T08:00:00-07:00"}
        response = client.post("/cases/{}/agent/run".format(case_id), json={"objective": "Search options"})
        assert response.status_code == 200
        search = response.json()["tool_results"][0]["result"]
        assert search["decision_code"] == "manual_review"
        assert search["eligible_alternatives"] == []
        with app.state.session_factory() as session:
            actions = session.scalar(select(func.count()).select_from(RecoveryAction))
            assert actions == 0
    finally:
        close(client)


def test_provider_quota_failure_uses_deterministic_fallback(tmp_path):
    _, client, _, response = invoke_agent(tmp_path, FakeAgent([QuotaError("private provider detail")]))
    try:
        assert response.status_code == 200
        body = response.json()
        assert body["metadata"]["provider"] == "deterministic_fallback"
        assert body["metadata"]["validation_status"] == "fallback_rate_limited"
        assert "private provider detail" not in response.text
    finally:
        close(client)


def test_gemini_agent_uses_declared_functions_and_disables_sdk_auto_execution():
    class Response:
        function_calls = []
        candidates = [type("Candidate", (), {"content": types.Content(role="model", parts=[types.Part.from_text(text="Ready")])})()]
        text = "Ready"

    class Models:
        def __init__(self):
            self.request = None

        def generate_content(self, **kwargs):
            self.request = kwargs
            return Response()

    class Client:
        def __init__(self):
            self.models = Models()

    client = Client()
    provider = GeminiCopilot("test-key", client_factory=lambda **_: client)
    turn = provider.agent_turn(["review this synthetic case"])
    assert turn["text"] == "Ready"
    request = client.models.request
    assert request["model"] == REQUIRED_MODEL
    config = request["config"]
    assert config.automatic_function_calling.disable is True
    assert config.tool_config.function_calling_config.allowed_function_names == [
        "search_synthetic_alternatives", "check_connection_feasibility",
        "compare_recovery_options", "propose_recovery_action",
    ]
    assert len(config.tools[0].function_declarations) == 4


def test_missing_key_fallback_reports_tools_but_never_proposes(tmp_path):
    app, client, _, response = invoke_agent(tmp_path, DeterministicCopilot(), allow_proposal=True)
    try:
        assert response.status_code == 200
        body = response.json()
        assert body["metadata"]["provider"] == "deterministic_fallback"
        assert body["metadata"]["fallback_used"] is True
        assert body["proposed_action_id"] is None
        assert all(item["name"] != "propose_recovery_action" for item in body["tool_results"])
        with app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(RecoveryAction)) == 0
    finally:
        close(client)


def test_proposal_permission_and_human_approval_gate_cannot_be_bypassed(tmp_path):
    # A valid proposal tool request is blocked unless the user explicitly permits a proposal.
    provider = FakeAgent([
        {"calls": [("propose_recovery_action", {"inventory_id": "SIM-001"})]},
        {"text": "Proposal request was blocked because user permission was not supplied."},
    ])
    app, client, case_id, response = invoke_agent(tmp_path, provider)
    try:
        assert response.status_code == 200
        assert response.json()["tool_results"][0]["status"] == "blocked"
        assert response.json()["tool_results"][0]["result"]["created"] is False
        with app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(RecoveryAction)) == 0
    finally:
        close(client)

    provider = FakeAgent([
        {"calls": [("propose_recovery_action", {"inventory_id": "SIM-001"})]},
        {"text": "A simulated proposal is waiting for human approval."},
    ])
    app, client, case_id, response = invoke_agent(tmp_path, provider, allow_proposal=True)
    try:
        body = response.json()
        action = client.get("/actions/{}".format(body["proposed_action_id"]))
        assert action.status_code == 200
        assert action.json()["state"] == "PENDING_APPROVAL"
        assert action.json()["executed_at"] is None
        assert body["tool_results"][0]["result"]["executed_by_agent"] is False
        # An approval function is outside the allowlist and cannot transition the action.
        app.state.copilot_provider = FakeAgent([
            {"calls": [("approve_action", {"action_id": body["proposed_action_id"]})]},
        ])
        attempted = client.post("/cases/{}/agent/run".format(case_id), json={"objective": "Approve that proposal"})
        assert attempted.status_code == 200
        assert attempted.json()["metadata"]["validation_status"] == "fallback_invalid_tool_call"
        still_pending = client.get("/actions/{}".format(body["proposed_action_id"])).json()
        assert still_pending["state"] == "PENDING_APPROVAL"
        assert still_pending["executed_at"] is None
        with app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(RecoveryAction)) == 1
    finally:
        close(client)
