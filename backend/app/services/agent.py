"""Bounded case agent: Gemini selects from local tools; backend validates and executes them."""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional
from uuid import uuid4

from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, ValidationError, constr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent, Booking, DisruptionCase, RecoveryDecision
from app.schemas.cases import CreateActionRequest
from app.schemas.copilot import AgentRunResponse, AgentToolResult, CopilotMetadata
from app.services.actions import create_proposed_action
from app.services.copilot import CopilotCaseContext, GeminiCopilot, build_case_context
from app.services.policy import DEFAULT_POLICY
from app.services.recovery import evaluate_case
from app.services.request_diagnostics import (
    current_request_trace_id,
    log_request_diagnostic,
    safe_diagnostic_label,
)

LOGGER = logging.getLogger(__name__)

MAX_AGENT_ITERATIONS = 4
MAX_AGENT_TOOL_CALLS = 4
MAX_AGENT_OUTPUT_CHARS = 1200
TOOL_NAMES = {
    "search_synthetic_alternatives",
    "check_connection_feasibility",
    "compare_recovery_options",
    "propose_recovery_action",
}
INVALID_TOOL_CALL_REASONS = {
    "malformed_tool_call",
    "unknown_tool",
    "invalid_tool_arguments",
    "decision_missing",
    "unknown_or_ineligible_alternative",
}


class StrictToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NoArgs(StrictToolArgs):
    pass


class CompareArgs(StrictToolArgs):
    alternative_ids: List[constr(strict=True, min_length=1, max_length=64)] = Field(min_length=1, max_length=4)


class ProposalArgs(StrictToolArgs):
    inventory_id: str = Field(min_length=1, max_length=64)


class ToolCall(StrictToolArgs):
    name: str = Field(min_length=1, max_length=64)
    arguments: Dict[str, Any] = Field(max_length=8)


class InvalidAgentToolCall(Exception):
    def __init__(self, reason_code: str):
        self.reason_code = (
            reason_code if reason_code in INVALID_TOOL_CALL_REASONS
            else "malformed_tool_call"
        )
        self.stage = "unknown_agent_stage"
        super().__init__(self.reason_code)


class AgentIterationLimit(Exception):
    pass


class AgentProviderRequestError(Exception):
    def __init__(self, error: Exception, stage: str):
        super().__init__("provider_request_failed")
        self.error = error
        self.stage = stage


class AgentProcessingError(Exception):
    def __init__(self, error: Exception, stage: str):
        super().__init__("agent_processing_failed")
        self.error = error
        self.stage = stage


def _safe_provider_message(error: Exception) -> str:
    message = getattr(error, "message", None)
    if not isinstance(message, str):
        return "unavailable"
    message = re.sub(
        r"(?i)\b(prompt|contents|request(?:\s+(?:body|content))?)"
        r"(?:\[[^\]]+\]|\.[\w.-]+)*\s*[:=]\s*.*",
        r"\1=[REDACTED]",
        message,
    )
    message = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", message)
    message = re.sub(
        r"(?i)\b(api[_ -]?key|authorization)\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        message,
    )
    message = re.sub(r"\bAIza[0-9A-Za-z_-]{20,}\b", "[REDACTED]", message)
    message = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[REDACTED_EMAIL]", message)
    message = re.sub(r"(?<!\w)\+?\d[\d(). -]{7,}\d(?!\w)", "[REDACTED_NUMBER]", message)
    message = re.sub(r"\s+", " ", message).strip()
    return message[:300] or "unavailable"


def _safe_processing_message(error: Exception) -> str:
    safe_messages = {
        "invalid_agent_summary",
        "missing_function_call_content",
        "agent_iteration_timeout",
    }
    message = str(error).strip()
    if message in safe_messages:
        return message
    return "local_value_error" if isinstance(error, ValueError) else "unavailable"


def _latest_decision(session: Session, case_id: int) -> Optional[RecoveryDecision]:
    return session.scalar(
        select(RecoveryDecision).where(RecoveryDecision.case_id == case_id)
        .order_by(RecoveryDecision.evaluated_at.desc(), RecoveryDecision.id.desc())
    )


def run_case_agent(
    session: Session,
    case: DisruptionCase,
    booking: Booking,
    provider,
    objective: str,
    allow_proposal: bool = False,
) -> AgentRunResponse:
    """Run a bounded advisory workflow. Any created action remains pending approval."""
    decision = _latest_decision(session, case.id)
    if decision is None:
        decision = evaluate_case(session, case, booking)
    context = build_case_context(session, case, booking)
    tool_results: List[AgentToolResult] = []
    proposed_action_id = None
    model_used = isinstance(provider, GeminiCopilot)
    validation_status = "validated"
    summary = ""

    if model_used:
        try:
            summary, proposed_action_id = _run_gemini_loop(
                session, case, context, provider, objective, allow_proposal,
                tool_results,
            )
        except AgentIterationLimit:
            validation_status = "fallback_iteration_limit"
        except InvalidAgentToolCall as error:
            validation_status = "fallback_invalid_tool_call"
            stage = error.stage
            if stage.startswith("execute_tool:"):
                tool_name = stage.partition(":")[2]
                stage = (
                    "execute_tool_" + tool_name
                    if tool_name in TOOL_NAMES else "execute_tool"
                )
            elif safe_diagnostic_label(stage) is None:
                stage = "unknown_agent_stage"
            log_request_diagnostic(
                LOGGER,
                "agent",
                current_request_trace_id(),
                stage,
                category=validation_status,
                reason_code=error.reason_code,
                exception_class=type(error).__name__,
            )
            session.add(AuditEvent(
                event_id=case.event_id,
                action="agent_tool_rejected",
                details={"case_id": case.id, "reason": "invalid_or_unregistered_tool_call"},
            ))
        except Exception as error:  # Provider and response failures never escape with details.
            if isinstance(error, (AgentProviderRequestError, AgentProcessingError)):
                provider_error = error.error
                error_stage = error.stage
            else:
                provider_error = error
                error_stage = "agent_processing"
            validation_status = _provider_fallback_status(provider_error)
            status = getattr(provider_error, "status_code", None) or getattr(provider_error, "code", None)
            code = getattr(provider_error, "code", None)
            provider_status = getattr(provider_error, "status", None)
            try:
                LOGGER.warning(
                    "Recovery agent run failed: trace_id=%s category=%s stage=%s exception_class=%s "
                    "http_status=%s provider_code=%s provider_status=%s",
                    current_request_trace_id(),
                    validation_status,
                    error_stage,
                    type(provider_error).__name__,
                    status if isinstance(status, int) else None,
                    code if isinstance(code, int) else None,
                    safe_diagnostic_label(provider_status),
                )
            except Exception:
                pass
        if not summary:
            if not tool_results:
                _run_fallback_tools(session, case, context, False, tool_results)
            summary = _deterministic_summary(context, tool_results)
            model_used = False
    else:
        validation_status = "fallback_no_key"
        _run_fallback_tools(session, case, context, allow_proposal, tool_results)
        summary = _deterministic_summary(context, tool_results)

    if proposed_action_id is None:
        proposed_action_id = next((
            item.result.get("action_id") for item in tool_results if item.result.get("action_id")
        ), None)

    if model_used and _claims_action_execution(summary):
        if not tool_results:
            _run_fallback_tools(session, case, context, False, tool_results)
        summary = _deterministic_summary(context, tool_results)
        model_used = False
        validation_status = "fallback_unsafe_summary"

    metadata = CopilotMetadata(
        provider="gemini" if model_used else "deterministic_fallback",
        model=getattr(provider, "model", None) if model_used else None,
        fallback_used=not model_used,
        validation_status=validation_status,
    )
    session.add(AuditEvent(
        event_id=case.event_id,
        action="agent_run_completed",
        details={
            "case_id": case.id,
            "provider": metadata.provider,
            "model": metadata.model,
            "fallback_used": metadata.fallback_used,
            "validation_status": metadata.validation_status,
            "tool_call_count": len(tool_results),
            "proposal_created": proposed_action_id is not None,
        },
    ))
    return AgentRunResponse(
        case_id=case.id,
        summary=summary[:MAX_AGENT_OUTPUT_CHARS],
        tool_results=tool_results[:MAX_AGENT_TOOL_CALLS],
        proposed_action_id=proposed_action_id,
        approval_required=True,
        metadata=metadata,
    )


def _run_gemini_loop(session, case, context, provider, objective, allow_proposal, results):
    stage = {"name": "build_initial_request_context"}
    try:
        return _run_gemini_loop_staged(
            session, case, context, provider, objective, allow_proposal, results, stage,
        )
    except InvalidAgentToolCall as error:
        error.stage = stage["name"]
        raise
    except (AgentIterationLimit, AgentProviderRequestError):
        raise
    except Exception as error:
        raise AgentProcessingError(error, stage["name"]) from error


def _run_gemini_loop_staged(session, case, context, provider, objective, allow_proposal, results, stage):
    initial = {
        "objective": objective,
        "case_evidence": _agent_context(context),
        "proposal_permission": bool(allow_proposal),
        "limits": {
            "max_tool_calls": MAX_AGENT_TOOL_CALLS,
            "tools": sorted(TOOL_NAMES),
            "simulated_only": True,
        },
    }
    contents: List[Any] = [json.dumps(initial, sort_keys=True)]
    started = time.monotonic()
    calls_used = 0
    proposal_id = None

    for iteration in range(MAX_AGENT_ITERATIONS):
        # The provider's configured request timeout bounds each network call.
        stage["name"] = "check_iteration_deadline"
        if time.monotonic() - started > MAX_AGENT_ITERATIONS * getattr(provider, "timeout_seconds", 15):
            raise TimeoutError("agent_iteration_timeout")
        request_stage = "initial_agent_request" if iteration == 0 else "follow_up_after_tool_result"
        try:
            turn = provider.agent_turn(contents)
        except Exception as error:
            raise AgentProviderRequestError(error, request_stage) from error
        stage["name"] = "read_model_turn"
        calls = turn.get("calls") or []
        text = turn.get("text")
        if not calls:
            stage["name"] = "validate_final_response"
            if not isinstance(text, str) or not text.strip() or len(text) > MAX_AGENT_OUTPUT_CHARS:
                raise ValueError("invalid_agent_summary")
            return text.strip(), proposal_id
        stage["name"] = "enforce_tool_call_limit"
        if calls_used + len(calls) > MAX_AGENT_TOOL_CALLS:
            raise AgentIterationLimit("tool_call_limit")

        # Validate the entire batch before executing any tool from it.
        validated = []
        for raw_call in calls:
            stage["name"] = "validate_tool_call"
            try:
                call = ToolCall.model_validate(raw_call)
            except ValidationError as error:
                raise InvalidAgentToolCall("malformed_tool_call") from error
            if call.name not in TOOL_NAMES:
                raise InvalidAgentToolCall("unknown_tool")
            arg_schema = {
                "search_synthetic_alternatives": NoArgs,
                "check_connection_feasibility": NoArgs,
                "compare_recovery_options": CompareArgs,
                "propose_recovery_action": ProposalArgs,
            }[call.name]
            stage["name"] = "validate_tool_arguments"
            try:
                args = arg_schema.model_validate(call.arguments)
            except ValidationError as error:
                raise InvalidAgentToolCall("invalid_tool_arguments") from error
            validated.append((call.name, args))

        stage["name"] = "validate_model_function_call_content"
        model_content = turn.get("content")
        if model_content is None:
            raise ValueError("missing_function_call_content")
        stage["name"] = "append_model_function_call_history"
        contents.append(model_content)
        response_parts = []
        for name, args in validated:
            stage["name"] = "execute_tool:{}".format(name)
            calls_used += 1
            result, status = _execute_tool(
                session, case, context, name, args, allow_proposal,
            )
            if name == "propose_recovery_action" and result.get("action_id"):
                proposal_id = result["action_id"]
            stage["name"] = "audit_tool:{}".format(name)
            _audit_tool(session, case, name, status)
            stage["name"] = "validate_tool_result:{}".format(name)
            results.append(AgentToolResult(name=name, status=status, result=result))
            stage["name"] = "build_function_response:{}".format(name)
            response_parts.append(types.Part.from_function_response(name=name, response=result))
        stage["name"] = "append_function_response_history"
        contents.append(types.Content(role="user", parts=response_parts))
    stage["name"] = "enforce_model_turn_limit"
    raise AgentIterationLimit("iteration_limit")


def _execute_tool(session, case, context, name, args, allow_proposal):
    decision = _latest_decision(session, case.id)
    if decision is None:
        raise InvalidAgentToolCall("decision_missing")
    if name == "search_synthetic_alternatives":
        return {
            "decision_code": decision.decision_code,
            "policy_id": decision.policy_id,
            "policy_version": decision.policy_version,
            "eligible_alternatives": decision.candidates,
            "rejected_candidate_reasons": decision.evidence.get("rejected_candidate_reasons", []),
            "inventory_is_simulated": True,
        }, "completed"
    if name == "check_connection_feasibility":
        assessments = decision.evidence.get("connection_assessments", [])
        return {
            "decision_code": decision.decision_code,
            "assessments": assessments,
            "requires_review": decision.decision_code == "escalate" or any(
                item.get("status") != "feasible" for item in assessments
            ),
            "policy_minimum_connection_minutes": next((
                item.get("minimum_minutes") for item in assessments if item.get("minimum_minutes") is not None
            ), DEFAULT_POLICY.minimum_connection_minutes),
            "timing_is_not_official_airport_validation": True,
        }, "completed"
    if name == "compare_recovery_options":
        candidates = {item["inventory_id"]: item for item in decision.candidates}
        requested = list(args.alternative_ids)
        if len(set(requested)) != len(requested) or any(item not in candidates for item in requested):
            raise InvalidAgentToolCall("unknown_or_ineligible_alternative")
        ranking = {item["inventory_id"]: index for index, item in enumerate(decision.candidates)}
        selected = sorted((candidates[item] for item in requested), key=lambda item: ranking[item["inventory_id"]])
        return {
            "decision_code": decision.decision_code,
            "policy_id": decision.policy_id,
            "policy_version": decision.policy_version,
            "ranking_is_policy_order": True,
            "options": selected,
            "inventory_is_simulated": True,
        }, "completed"
    if name == "propose_recovery_action":
        if not allow_proposal:
            return {"created": False, "reason": "explicit_proposal_permission_required"}, "blocked"
        if decision.decision_code != "recommend_for_review":
            return {"created": False, "reason": "decision_not_eligible_for_rebooking"}, "blocked"
        inventory_id = args.inventory_id
        if not any(item.get("inventory_id") == inventory_id and item.get("simulated") is True
                   for item in decision.candidates):
            return {"created": False, "reason": "alternative_not_in_persisted_eligible_decision"}, "blocked"
        request = CreateActionRequest(
            action_type="REBOOK",
            idempotency_key="agent:{}:{}:REBOOK:{}".format(case.id, decision.id, inventory_id),
            decision_id=decision.id,
            inventory_id=inventory_id,
        )
        action, created = create_proposed_action(session, case, request)
        if action.state != "PENDING_APPROVAL":
            return {
                "created": False,
                "reason": "matching_action_is_not_pending_approval",
                "existing_state": action.state,
                "simulated": True,
                "executed_by_agent": False,
            }, "blocked"
        return {
            "created": created,
            "action_id": action.action_id,
            "state": action.state,
            "simulated": True,
            "requires_human_approval": True,
            "executed_by_agent": False,
        }, "completed"
    raise InvalidAgentToolCall("unknown_tool")


def _agent_context(context: CopilotCaseContext) -> Dict[str, Any]:
    decision = context.decision or {}
    return {
        "case_id": context.case_id,
        "flight_id": context.flight_id,
        "case_status": context.case_status,
        "decision": {
            "decision_code": decision.get("decision_code"),
            "policy_id": decision.get("policy_id"),
            "policy_version": decision.get("policy_version"),
            "reason_codes": decision.get("reason_codes", []),
        },
        "inventory_is_synthetic": True,
    }


def _run_fallback_tools(session, case, context, allow_proposal, results):
    # No-key fallback reports current persisted facts but never guesses a proposal.
    for name, args in (
        ("search_synthetic_alternatives", NoArgs()),
        ("check_connection_feasibility", NoArgs()),
    ):
        result, status = _execute_tool(session, case, context, name, args, False)
        _audit_tool(session, case, name, status)
        results.append(AgentToolResult(name=name, status=status, result=result))
    if context.decision and context.decision.get("candidates"):
        ids = [item["inventory_id"] for item in context.decision["candidates"][:4]]
        result, status = _execute_tool(session, case, context, "compare_recovery_options", CompareArgs(alternative_ids=ids), False)
        _audit_tool(session, case, "compare_recovery_options", status)
        results.append(AgentToolResult(name="compare_recovery_options", status=status, result=result))


def _audit_tool(session, case, name, status):
    session.add(AuditEvent(
        event_id=case.event_id,
        action="agent_tool_called" if status == "completed" else "agent_tool_blocked",
        details={"case_id": case.id, "tool_name": name, "status": status},
    ))


def _deterministic_summary(context, results):
    decision = context.decision or {}
    code = decision.get("decision_code", "manual_review")
    eligible = decision.get("candidates", [])
    text = "Deterministic policy result: {}. {} eligible simulated alternative(s) are recorded.".format(
        code, len(eligible)
    )
    if decision.get("explanation"):
        text += " {}".format(decision["explanation"])
    if any(item.result.get("action_id") for item in results):
        text += " A proposal is pending explicit human approval; no action has been taken."
    else:
        text += " No action has been taken."
    return text


def _claims_action_execution(text):
    action_claim = r"\b(?:approved|executed|booked|rebooked|refund issued|message sent|ticket confirmed)\b"
    if not re.search(action_claim, text, re.I):
        return False
    return not bool(re.search(
        r"\b(?:no|not|never|has not|hasn't|was not|wasn't)\b.{0,40}" + action_claim,
        text, re.I,
    ))


def _provider_fallback_status(error):
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    description = (
        str(status or "") + " " +
        str(getattr(error, "status", "") or "") + " " +
        type(error).__name__
    ).lower()
    if status == 429 or "quota" in description or "rate" in description or "resource_exhausted" in description:
        return "fallback_rate_limited"
    if status == 504 or "timeout" in description or "deadline_exceeded" in description or isinstance(error, TimeoutError):
        return "fallback_timeout"
    if status in (401, 403) or "permissiondenied" in description or "unauthenticated" in description:
        return "fallback_auth_error"
    return "fallback_provider_error"
