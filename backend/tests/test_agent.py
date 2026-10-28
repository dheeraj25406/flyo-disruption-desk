from google.genai import types
from sqlalchemy import func, select

from app.main import create_app
from app.models import AuditEvent, RecoveryAction
from conftest import seed_legacy_test_data
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
        content = turn.get("content")
        if content is None:
            content = types.Content(role="model", parts=parts) if calls else types.Content(role="model", parts=[types.Part.from_text(text=turn.get("text", ""))])
        return {"calls": calls, "content": content, "text": turn.get("text")}


class QuotaError(Exception):
    status_code = 429


class DeadlineError(Exception):
    status_code = 504
    status = "DEADLINE_EXCEEDED"
    message = "Deadline expired before operation could complete."


def client_error(message):
    from google.genai.errors import ClientError

    return ClientError(400, {
        "error": {
            "code": 400,
            "status": "INVALID_ARGUMENT",
            "message": message,
        },
    })


def invoke_agent(tmp_path, provider, allow_proposal=False, trace_id=None):
    app = create_app("sqlite:///{}".format(tmp_path / "agent.db"), seed_data=False, copilot_provider=provider)
    from fastapi.testclient import TestClient
    client = TestClient(app)
    client.__enter__()
    seed_legacy_test_data(app.state.session_factory)
    event = client.post("/webhooks/flight-alerts", json={
        "event_id": "evt-agent-test", "fid": "FL-1001", "changes": {"status": "cancel"}
    })
    assert event.status_code == 200
    case_id = next(item["id"] for item in client.get("/cases").json() if item["booking_ref"] == "BK-101")
    headers = {"X-Request-ID": trace_id} if trace_id else None
    response = client.post(
        "/cases/{}/agent/run".format(case_id),
        headers=headers,
        json={
            "objective": "Review the disruption and available synthetic recovery options.",
            "allow_proposal": allow_proposal,
        },
    )
    return app, client, case_id, response


def close(client):
    client.__exit__(None, None, None)


def assert_read_only_fallback(body):
    names = [item["name"] for item in body["tool_results"]]
    assert "search_synthetic_alternatives" in names
    assert "check_connection_feasibility" in names
    assert "propose_recovery_action" not in names
    assert body["proposed_action_id"] is None


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


def test_unknown_tool_name_uses_fallback_without_dispatch(tmp_path, caplog):
    caplog.set_level("INFO")
    trace_id = "12345678-1234-5678-1234-567812345678"
    private_argument = "Passenger details and private request content"
    provider = FakeAgent([{"calls": [("execute_sql", {"query": private_argument})]}])
    app, client, _, response = invoke_agent(tmp_path, provider, trace_id=trace_id)
    try:
        assert response.status_code == 200
        body = response.json()
        assert body["metadata"]["validation_status"] == "fallback_invalid_tool_call"
        assert body["metadata"]["fallback_used"] is True
        assert_read_only_fallback(body)
        with app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(RecoveryAction)) == 0
            assert session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "agent_tool_rejected")) == 1
        diagnostic = next(line for line in caplog.text.splitlines() if "reason_code=unknown_tool" in line)
        assert trace_id in diagnostic
        assert "operation=agent" in diagnostic
        assert "stage=validate_tool_call" in diagnostic
        assert "category=fallback_invalid_tool_call" in diagnostic
        assert "exception_class=InvalidAgentToolCall" in diagnostic
        assert private_argument not in caplog.text
    finally:
        close(client)


def test_invalid_tool_arguments_use_fallback(tmp_path, caplog):
    caplog.set_level("INFO")
    private_argument = "private-passenger-reference"
    provider = FakeAgent([{"calls": [("search_synthetic_alternatives", {"fid": private_argument})]}])
    _, client, _, response = invoke_agent(tmp_path, provider)
    try:
        assert response.status_code == 200
        body = response.json()
        assert body["metadata"]["validation_status"] == "fallback_invalid_tool_call"
        assert_read_only_fallback(body)
        assert "stage=validate_tool_arguments" in caplog.text
        assert "reason_code=invalid_tool_arguments" in caplog.text
        assert "exception_class=InvalidAgentToolCall" in caplog.text
        assert private_argument not in caplog.text
    finally:
        close(client)


def test_compare_tool_rejects_model_invented_alternative_id(tmp_path, caplog):
    caplog.set_level("INFO")
    fabricated_id = "SIM-PRIVATE-FABRICATION"
    provider = FakeAgent([{"calls": [("compare_recovery_options", {"alternative_ids": [fabricated_id]})]}])
    _, client, _, response = invoke_agent(tmp_path, provider)
    try:
        assert response.status_code == 200
        body = response.json()
        assert body["metadata"]["validation_status"] == "fallback_invalid_tool_call"
        assert_read_only_fallback(body)
        assert "stage=execute_tool_compare_recovery_options" in caplog.text
        assert "reason_code=unknown_or_ineligible_alternative" in caplog.text
        assert "exception_class=InvalidAgentToolCall" in caplog.text
        assert fabricated_id not in caplog.text
    finally:
        close(client)


def test_compare_tool_returns_requested_options_in_policy_order(tmp_path):
    model_content = types.Content(
        role="model",
        parts=[types.Part(
            function_call=types.FunctionCall(
                name="compare_recovery_options",
                args={"alternative_ids": ["SIM-002", "SIM-001"]},
            ),
            thought_signature=b"opaque-thought-signature",
        )],
    )
    provider = FakeAgent([
        {
            "calls": [("compare_recovery_options", {
                "alternative_ids": ["SIM-002", "SIM-001"],
            })],
            "content": model_content,
        },
        {"text": "Both eligible simulated options were compared in deterministic policy order."},
    ])
    _, client, _, response = invoke_agent(tmp_path, provider)
    try:
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["metadata"]["provider"] == "gemini"
        assert body["metadata"]["fallback_used"] is False
        assert body["metadata"]["validation_status"] == "validated"
        assert len(body["tool_results"]) == 1
        result = body["tool_results"][0]
        assert result["name"] == "compare_recovery_options"
        assert result["status"] == "completed"
        assert [item["inventory_id"] for item in result["result"]["options"]] == [
            "SIM-001", "SIM-002",
        ]
        assert result["result"]["ranking_is_policy_order"] is True
        assert result["result"]["inventory_is_simulated"] is True
        assert body["proposed_action_id"] is None
        assert len(provider.turn_calls) == 2
        history = provider.turn_calls[1]
        assert history[-2] is model_content
        assert history[-2].role == "model"
        assert history[-2].parts[0].function_call.name == "compare_recovery_options"
        assert history[-2].parts[0].thought_signature == b"opaque-thought-signature"
        function_response_content = history[-1]
        assert function_response_content.role == "user"
        assert len(function_response_content.parts) == 1
        function_response = function_response_content.parts[0].function_response
        assert function_response.name == "compare_recovery_options"
        response_data = function_response.response
        assert [item["inventory_id"] for item in response_data["options"]] == [
            "SIM-001", "SIM-002",
        ]
        assert "private provider detail" not in response.text
    finally:
        close(client)


def test_follow_up_timeout_preserves_tools_and_reports_deterministic_fallback(tmp_path):
    provider = FakeAgent([
        {"calls": [
            ("search_synthetic_alternatives", {}),
            ("check_connection_feasibility", {}),
        ]},
        DeadlineError("private provider details"),
    ])
    app, client, _, response = invoke_agent(tmp_path, provider)
    try:
        assert response.status_code == 200
        body = response.json()
        assert body["metadata"]["provider"] == "deterministic_fallback"
        assert body["metadata"]["model"] is None
        assert body["metadata"]["fallback_used"] is True
        assert body["metadata"]["validation_status"] == "fallback_timeout"
        assert [item["name"] for item in body["tool_results"]] == [
            "search_synthetic_alternatives",
            "check_connection_feasibility",
        ]
        assert body["proposed_action_id"] is None
        assert "Deterministic policy result:" in body["summary"]
        assert "private provider details" not in response.text
        with app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(RecoveryAction)) == 0
    finally:
        close(client)


def test_invalid_final_model_response_logs_safe_processing_stage(tmp_path, caplog):
    provider = FakeAgent([
        {"calls": [("search_synthetic_alternatives", {})]},
        {"text": ""},
    ])
    _, client, _, response = invoke_agent(tmp_path, provider)
    try:
        assert response.status_code == 200
        body = response.json()
        assert body["metadata"]["provider"] == "deterministic_fallback"
        assert body["metadata"]["fallback_used"] is True
        assert body["metadata"]["validation_status"] == "fallback_provider_error"
        assert [item["name"] for item in body["tool_results"]] == [
            "search_synthetic_alternatives",
        ]
        assert "stage=validate_final_response" in caplog.text
        assert "exception_class=ValueError" in caplog.text
        assert "provider_message=" not in caplog.text
        assert body["proposed_action_id"] is None
    finally:
        close(client)


def test_function_response_value_error_logs_operation_without_raw_message(
    tmp_path, caplog, monkeypatch,
):
    import app.services.agent as agent_service

    def fail_function_response(*args, **kwargs):
        raise ValueError("private serialized passenger payload")

    monkeypatch.setattr(
        agent_service.types.Part,
        "from_function_response",
        staticmethod(fail_function_response),
    )
    provider = FakeAgent([{"calls": [("search_synthetic_alternatives", {})]}])
    app, client, _, response = invoke_agent(tmp_path, provider)
    try:
        assert response.status_code == 200
        body = response.json()
        assert body["metadata"]["provider"] == "deterministic_fallback"
        assert body["metadata"]["fallback_used"] is True
        assert body["metadata"]["validation_status"] == "fallback_provider_error"
        assert [item["name"] for item in body["tool_results"]] == [
            "search_synthetic_alternatives",
        ]
        assert "stage=build_function_response:search_synthetic_alternatives" in caplog.text
        assert "exception_class=ValueError" in caplog.text
        assert "provider_message=" not in caplog.text
        assert "private serialized passenger payload" not in caplog.text
        assert body["proposed_action_id"] is None
        with app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(RecoveryAction)) == 0
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
    app = create_app("sqlite:///{}".format(tmp_path / "agent.db"), seed_data=False, copilot_provider=provider)
    from fastapi.testclient import TestClient
    client = TestClient(app)
    client.__enter__()
    seed_legacy_test_data(app.state.session_factory)
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


def test_provider_quota_failure_uses_deterministic_fallback(tmp_path, caplog):
    app, client, _, response = invoke_agent(tmp_path, FakeAgent([QuotaError("private provider detail")]))
    try:
        assert response.status_code == 200
        body = response.json()
        assert body["metadata"]["provider"] == "deterministic_fallback"
        assert body["metadata"]["fallback_used"] is True
        assert body["metadata"]["validation_status"] == "fallback_rate_limited"
        assert_read_only_fallback(body)
        tool_names = [item["name"] for item in body["tool_results"]]
        assert all(item["status"] == "completed" for item in body["tool_results"])
        with app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(RecoveryAction)) == 0
            assert session.scalar(select(func.count()).select_from(AuditEvent).where(
                AuditEvent.action == "agent_tool_called"
            )) == len(body["tool_results"])
        assert "private provider detail" not in response.text
        assert "category=fallback_rate_limited" in caplog.text
        assert "exception_class=QuotaError" in caplog.text
        assert "http_status=429" in caplog.text
        assert "private provider detail" not in caplog.text
    finally:
        close(client)


def test_provider_failure_preserves_existing_tool_results_without_rerunning_tools(tmp_path):
    provider = FakeAgent([
        {"calls": [("search_synthetic_alternatives", {})]},
        QuotaError("private provider detail"),
    ])
    app, client, _, response = invoke_agent(tmp_path, provider)
    try:
        assert response.status_code == 200
        body = response.json()
        assert body["metadata"]["provider"] == "deterministic_fallback"
        assert body["metadata"]["fallback_used"] is True
        assert body["metadata"]["validation_status"] == "fallback_rate_limited"
        assert [item["name"] for item in body["tool_results"]] == [
            "search_synthetic_alternatives",
        ]
        assert body["proposed_action_id"] is None
        with app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(RecoveryAction)) == 0
            assert session.scalar(select(func.count()).select_from(AuditEvent).where(
                AuditEvent.action == "agent_tool_called"
            )) == 1
            assert session.scalar(select(func.count()).select_from(AuditEvent).where(
                AuditEvent.action == "agent_tool_blocked"
            )) == 0
        assert "private provider detail" not in response.text
    finally:
        close(client)


def test_initial_agent_400_logs_sanitized_provider_error_and_falls_back(tmp_path, caplog):
    error = client_error(
        "Invalid tool declaration for compare_recovery_options; "
        "api_key=SECRET_VALUE passenger@example.com; prompt=private objective text"
    )
    app, client, _, response = invoke_agent(tmp_path, FakeAgent([error]))
    try:
        assert response.status_code == 200
        body = response.json()
        assert body["metadata"]["provider"] == "deterministic_fallback"
        assert body["metadata"]["fallback_used"] is True
        assert body["metadata"]["validation_status"] == "fallback_provider_error"
        assert_read_only_fallback(body)
        assert "stage=initial_agent_request" in caplog.text
        assert "exception_class=ClientError" in caplog.text
        assert "http_status=400" in caplog.text
        assert "provider_code=400" in caplog.text
        assert "provider_status=INVALID_ARGUMENT" in caplog.text
        assert "provider_message=" not in caplog.text
        assert "Invalid tool declaration for compare_recovery_options" not in caplog.text
        assert "SECRET_VALUE" not in caplog.text
        assert "passenger@example.com" not in caplog.text
        assert "private objective text" not in caplog.text
        with app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(RecoveryAction)) == 0
    finally:
        close(client)


def test_follow_up_agent_400_identifies_request_after_tool_result(tmp_path, caplog):
    provider = FakeAgent([
        {"calls": [("search_synthetic_alternatives", {})]},
        client_error("Invalid function response payload."),
    ])
    _, client, _, response = invoke_agent(tmp_path, provider)
    try:
        assert response.status_code == 200
        body = response.json()
        assert body["metadata"]["provider"] == "deterministic_fallback"
        assert body["metadata"]["validation_status"] == "fallback_provider_error"
        assert [item["name"] for item in body["tool_results"]] == [
            "search_synthetic_alternatives",
        ]
        assert "stage=follow_up_after_tool_result" in caplog.text
        assert "provider_status=INVALID_ARGUMENT" in caplog.text
        assert "provider_message=" not in caplog.text
        assert "Invalid function response payload." not in caplog.text
    finally:
        close(client)


def test_agent_trace_id_correlates_endpoint_and_provider_failure(tmp_path, caplog):
    caplog.set_level("INFO")
    trace_id = "12345678-1234-5678-1234-567812345678"
    secret_detail = "passenger name and private provider message"
    _, client, _, response = invoke_agent(
        tmp_path, FakeAgent([client_error(secret_detail)]), trace_id=trace_id,
    )
    try:
        assert response.status_code == 200
        assert response.json()["metadata"]["validation_status"] == "fallback_provider_error"
        lines = [line for line in caplog.text.splitlines() if trace_id in line]
        assert len(lines) == 5
        endpoint_lines = [line for line in lines if "operation=agent" in line]
        assert len(endpoint_lines) == 4
        assert any("stage=endpoint_entry" in line for line in endpoint_lines)
        assert any("stage=provider_call_start" in line for line in endpoint_lines)
        assert any("stage=provider_return" in line and "category=fallback_provider_error" in line for line in endpoint_lines)
        assert any("stage=endpoint_exit" in line for line in endpoint_lines)
        assert any(
            "Recovery agent run failed:" in line and "stage=initial_agent_request" in line
            for line in lines
        )
        failure_line = next(line for line in lines if "Recovery agent run failed:" in line)
        assert "exception_class=ClientError" in failure_line
        assert "http_status=400" in failure_line
        assert "provider_code=400" in failure_line
        assert "provider_status=INVALID_ARGUMENT" in failure_line
        assert "provider_message=" not in failure_line
        assert secret_detail not in caplog.text
        assert secret_detail not in response.text
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
    function_calling = config.tool_config.function_calling_config
    assert function_calling.mode == "AUTO"
    assert function_calling.allowed_function_names is None
    assert "request them together in one turn" in config.system_instruction
    assert "return a concise final text response immediately" in config.system_instruction
    assert [item.name for item in config.tools[0].function_declarations] == [
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
