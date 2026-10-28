import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AuditEvent, Booking, DisruptionCase, DisruptionEvent, RecoveryAction, RecoveryDecision
from app.schemas.cases import (
    ApprovalRequest, CaseCopilotResponse, CaseDetail, CaseListItem, CreateActionRequest,
    RecoveryActionResponse, RecoveryEvaluation, RejectionRequest, AuditTimelineItem,
)
from app.schemas.flight_alert import FlightAlert
from app.schemas.copilot import (
    AgentRunRequest, AgentRunResponse, AskCopilotRequest,
    CopilotAnalysisResponse, CopilotAskResponse,
)
from app.services.actions import (
    ActionEligibilityError, ActionIdempotencyConflict, InvalidActionTransition,
    approve_and_execute, create_proposed_action, reject_action,
)
from app.services.copilot import build_case_context, draft_case_message
from app.services.disruptions import UnknownFlightError, process_cancellation
from app.services.agent import run_case_agent
from app.services.request_diagnostics import (
    log_request_diagnostic,
    reset_request_trace_id,
    request_trace_id,
    set_request_trace_id,
)
from app.services.recovery import evaluate_case

router = APIRouter()
LOGGER = logging.getLogger(__name__)

def _trace_endpoint(
    operation: str,
    trace_id: str,
    stage: str,
    *,
    category="none",
    http_status=None,
    exception_class=None,
    provider_code=None,
    provider_status=None,
) -> None:
    log_request_diagnostic(
        LOGGER, operation, trace_id, stage,
        category=category, http_status=http_status,
        exception_class=exception_class,
        provider_code=provider_code, provider_status=provider_status,
    )


def _fallback_category(result) -> str:
    metadata = getattr(result, "metadata", None)
    if metadata is None:
        return "validated"
    return metadata.validation_status if metadata.fallback_used else "validated"


def _endpoint_error_fields(error: Exception):
    status = error.status_code if isinstance(error, HTTPException) else (
        getattr(error, "status_code", None) or getattr(error, "code", None) or 500
    )
    if not isinstance(status, int):
        status = 500
    category = "http_error" if isinstance(error, HTTPException) else "endpoint_error"
    return status, category


def get_session(request: Request):
    factory = request.app.state.session_factory
    with factory() as session:
        yield session


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/webhooks/flight-alerts")
def receive_flight_alert(alert: FlightAlert, session: Session = Depends(get_session)):
    try:
        duplicate, cases_created = process_cancellation(session, alert)
        session.commit()
    except UnknownFlightError:
        session.rollback()
        raise HTTPException(status_code=404, detail=f"Unknown flight: {alert.fid}")
    except Exception:
        session.rollback()
        raise

    return {
        "event_id": alert.event_id,
        "status": "already_processed" if duplicate else "processed",
        "duplicate": duplicate,
        "cases_created": cases_created,
    }


@router.get("/cases", response_model=list[CaseListItem])
def list_cases(session: Session = Depends(get_session)):
    cases = session.scalars(select(DisruptionCase).order_by(DisruptionCase.created_at.desc(), DisruptionCase.id)).all()
    results = []
    for case in cases:
        booking = session.get(Booking, case.booking_id)
        if booking is not None:
            results.append(CaseListItem(
                id=case.id,
                booking_ref=booking.booking_ref,
                passenger_name=booking.passenger_name,
                flight_id=case.flight_id,
                status=case.status,
                created_at=case.created_at,
            ))
    return results


@router.get("/cases/{case_id}", response_model=CaseDetail)
def get_case(case_id: int, session: Session = Depends(get_session)):
    case = session.get(DisruptionCase, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"Case not found: {case_id}")
    booking = session.get(Booking, case.booking_id)
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking associated with case was not found")
    event = session.scalar(select(DisruptionEvent).where(DisruptionEvent.event_id == case.event_id))
    timeline = []
    if event is not None:
        timeline.append(AuditTimelineItem(
            at=event.received_at,
            action="flight_event_received",
            details={"event_id": event.event_id, "fid": event.fid, "status": event.status},
        ))
    audit_events = session.scalars(
        select(AuditEvent).where(AuditEvent.event_id == case.event_id).order_by(AuditEvent.created_at, AuditEvent.id)
    ).all()
    latest_decision = session.scalar(
        select(RecoveryDecision)
        .where(RecoveryDecision.case_id == case.id)
        .order_by(RecoveryDecision.evaluated_at.desc(), RecoveryDecision.id.desc())
    )
    for audit in audit_events:
        details = audit.details or {}
        case_specific = details.get("case_id") == case.id
        relevant_flight = audit.action == "flight_cancelled" and details.get("fid") == case.flight_id
        relevant_booking = audit.action == "case_created" and details.get("booking_ref") == booking.booking_ref
        if not (case_specific or relevant_flight or relevant_booking):
            continue
        action = "flight_event_processed" if audit.action == "flight_cancelled" else audit.action
        timeline.append(AuditTimelineItem(at=audit.created_at, action=action, details=details))
    timeline.sort(key=lambda item: item.at.timestamp())
    return CaseDetail(
        id=case.id,
        booking_ref=booking.booking_ref,
        passenger_name=booking.passenger_name,
        flight_id=case.flight_id,
        status=case.status,
        created_at=case.created_at,
        event_id=case.event_id,
        disruption_reason=(event.payload or {}).get("reason") if event is not None else None,
        itinerary=sorted(booking.itinerary or [], key=lambda leg: leg.get("sequence", 0)),
        passenger_constraints=booking.passenger_constraints or {},
        audit_timeline=timeline,
        latest_decision=RecoveryEvaluation(
            case_id=case.id,
            decision_id=latest_decision.id,
            decision_code=latest_decision.decision_code,
            explanation=latest_decision.explanation,
            reason_codes=latest_decision.reason_codes,
            policy_id=latest_decision.policy_id,
            policy_version=latest_decision.policy_version,
            requires_human_approval=latest_decision.requires_human_approval,
            simulated_inventory=True,
            connection_assessments=latest_decision.evidence.get("connection_assessments", []),
            candidates=latest_decision.candidates,
            side_effects_executed=False,
            evaluated_at=latest_decision.evaluated_at,
        ) if latest_decision is not None else None,
    )


@router.post("/cases/{case_id}/evaluate", response_model=RecoveryEvaluation)
def evaluate_recovery(case_id: int, session: Session = Depends(get_session)):
    case = session.get(DisruptionCase, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"Case not found: {case_id}")
    booking = session.get(Booking, case.booking_id)
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking associated with case was not found")
    decision = evaluate_case(session, case, booking)
    session.commit()
    assessments = decision.evidence["connection_assessments"]
    return RecoveryEvaluation(
        case_id=case.id,
        decision_id=decision.id,
        decision_code=decision.decision_code,
        explanation=decision.explanation,
        reason_codes=decision.reason_codes,
        policy_id=decision.policy_id,
        policy_version=decision.policy_version,
        requires_human_approval=decision.requires_human_approval,
        simulated_inventory=True,
        connection_assessments=assessments,
        candidates=decision.candidates,
        side_effects_executed=False,
        evaluated_at=decision.evaluated_at,
    )


def _copilot_case_context(session: Session, case_id: int, ensure_evaluation: bool = False):
    case = session.get(DisruptionCase, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"Case not found: {case_id}")
    booking = session.get(Booking, case.booking_id)
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking associated with case was not found")
    if ensure_evaluation and session.scalar(
        select(RecoveryDecision.id)
        .where(RecoveryDecision.case_id == case.id)
        .limit(1)
    ) is None:
        evaluate_case(session, case, booking)
    return case, build_case_context(session, case, booking)


def _audit_copilot(session: Session, case: DisruptionCase, action: str, result):
    metadata = result.metadata
    session.add(AuditEvent(
        event_id=case.event_id,
        action=action,
        details={
            "case_id": case.id,
            "provider": metadata.provider,
            "model": metadata.model,
            "fallback_used": metadata.fallback_used,
            "validation_status": metadata.validation_status,
        },
    ))


@router.post("/cases/{case_id}/copilot/analyze", response_model=CopilotAnalysisResponse)
def analyze_case(case_id: int, request: Request, session: Session = Depends(get_session)):
    trace_id = request_trace_id(request)
    token = set_request_trace_id(trace_id)
    _trace_endpoint("analyze", trace_id, "endpoint_entry")
    try:
        case, context = _copilot_case_context(session, case_id, ensure_evaluation=True)
        _trace_endpoint("analyze", trace_id, "provider_call_start")
        result = request.app.state.copilot_provider.analyze(context)
        category = _fallback_category(result)
        _trace_endpoint("analyze", trace_id, "provider_return", http_status=200, category=category)
        _audit_copilot(session, case, "copilot_analysis_generated", result)
        session.commit()
        _trace_endpoint("analyze", trace_id, "endpoint_exit", http_status=200, category=category)
        return result
    except Exception as error:
        status, category = _endpoint_error_fields(error)
        _trace_endpoint(
            "analyze", trace_id, "endpoint_exception", http_status=status,
            category=category, exception_class=type(error).__name__,
            provider_code=getattr(error, "code", None),
            provider_status=getattr(error, "status", None),
        )
        raise
    finally:
        reset_request_trace_id(token)


@router.post("/cases/{case_id}/copilot/ask", response_model=CopilotAskResponse)
def ask_case(case_id: int, body: AskCopilotRequest, request: Request, session: Session = Depends(get_session)):
    trace_id = request_trace_id(request)
    token = set_request_trace_id(trace_id)
    _trace_endpoint("ask", trace_id, "endpoint_entry")
    try:
        case, context = _copilot_case_context(session, case_id, ensure_evaluation=True)
        _trace_endpoint("ask", trace_id, "provider_call_start")
        result = request.app.state.copilot_provider.answer(context, body.question)
        category = _fallback_category(result)
        _trace_endpoint("ask", trace_id, "provider_return", http_status=200, category=category)
        _audit_copilot(session, case, "copilot_question_answered", result)
        session.commit()
        _trace_endpoint("ask", trace_id, "endpoint_exit", http_status=200, category=category)
        return result
    except Exception as error:
        status, category = _endpoint_error_fields(error)
        _trace_endpoint(
            "ask", trace_id, "endpoint_exception", http_status=status,
            category=category, exception_class=type(error).__name__,
            provider_code=getattr(error, "code", None),
            provider_status=getattr(error, "status", None),
        )
        raise
    finally:
        reset_request_trace_id(token)


@router.post("/cases/{case_id}/agent/run", response_model=AgentRunResponse)
def run_recovery_agent(
    case_id: int,
    body: AgentRunRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    trace_id = request_trace_id(request)
    token = set_request_trace_id(trace_id)
    _trace_endpoint("agent", trace_id, "endpoint_entry")
    try:
        case = session.get(DisruptionCase, case_id)
        if case is None:
            raise HTTPException(status_code=404, detail=f"Case not found: {case_id}")
        booking = session.get(Booking, case.booking_id)
        if booking is None:
            raise HTTPException(status_code=404, detail="Booking associated with case was not found")
        _trace_endpoint("agent", trace_id, "provider_call_start")
        result = run_case_agent(
            session, case, booking, request.app.state.copilot_provider,
            body.objective, body.allow_proposal,
        )
        category = _fallback_category(result)
        _trace_endpoint("agent", trace_id, "provider_return", http_status=200, category=category)
        session.commit()
        _trace_endpoint("agent", trace_id, "endpoint_exit", http_status=200, category=category)
        return result
    except Exception as error:
        status, category = _endpoint_error_fields(error)
        _trace_endpoint(
            "agent", trace_id, "endpoint_exception", http_status=status,
            category=category, exception_class=type(error).__name__,
            provider_code=getattr(error, "code", None),
            provider_status=getattr(error, "status", None),
        )
        raise
    finally:
        reset_request_trace_id(token)


@router.post("/cases/{case_id}/actions", response_model=RecoveryActionResponse, status_code=201)
def propose_action(
    case_id: int,
    request: CreateActionRequest,
    response: Response,
    session: Session = Depends(get_session),
):
    case = session.get(DisruptionCase, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"Case not found: {case_id}")
    try:
        action, created = create_proposed_action(session, case, request)
        session.commit()
    except ActionEligibilityError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(error))
    except ActionIdempotencyConflict as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(error))
    except IntegrityError:
        # The unique idempotency constraint is the final guard for concurrent retries.
        session.rollback()
        retry_case = session.get(DisruptionCase, case_id)
        if retry_case is None:
            raise HTTPException(status_code=404, detail=f"Case not found: {case_id}")
        try:
            action, created = create_proposed_action(session, retry_case, request)
            session.commit()
        except ActionIdempotencyConflict as error:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(error))
        except ActionEligibilityError as error:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(error))
    if not created:
        response.status_code = 200
    return action


@router.get("/actions/{action_id}", response_model=RecoveryActionResponse)
def get_action(action_id: str, session: Session = Depends(get_session)):
    action = session.get(RecoveryAction, action_id)
    if action is None:
        raise HTTPException(status_code=404, detail=f"Action not found: {action_id}")
    return action


@router.post("/actions/{action_id}/approve", response_model=RecoveryActionResponse)
def approve_action(
    action_id: str,
    request: ApprovalRequest,
    session: Session = Depends(get_session),
):
    try:
        action = approve_and_execute(session, action_id, request.approved_by)
        if action is None:
            session.rollback()
            raise HTTPException(status_code=404, detail=f"Action not found: {action_id}")
        session.commit()
        return action
    except InvalidActionTransition as error:
        # Keep a durable audit record of rejected transition attempts.
        session.commit()
        raise HTTPException(status_code=409, detail=str(error))


@router.post("/actions/{action_id}/reject", response_model=RecoveryActionResponse)
def reject_pending_action(
    action_id: str,
    request: RejectionRequest = None,
    session: Session = Depends(get_session),
):
    try:
        action = reject_action(session, action_id, request.reason if request else None)
        if action is None:
            session.rollback()
            raise HTTPException(status_code=404, detail=f"Action not found: {action_id}")
        session.commit()
        return action
    except InvalidActionTransition as error:
        session.commit()
        raise HTTPException(status_code=409, detail=str(error))


@router.post("/cases/{case_id}/draft-message", response_model=CaseCopilotResponse)
def create_case_draft(case_id: int, request: Request, session: Session = Depends(get_session)):
    case = session.get(DisruptionCase, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"Case not found: {case_id}")
    booking = session.get(Booking, case.booking_id)
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking associated with case was not found")
    result = draft_case_message(session, case, booking, request.app.state.copilot_provider)
    if result is None:
        session.rollback()
        raise HTTPException(status_code=409, detail="Evaluate this case before generating a draft")
    session.commit()
    return result
