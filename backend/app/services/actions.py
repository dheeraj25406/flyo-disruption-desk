from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import AuditEvent, DisruptionCase, RecoveryAction, RecoveryDecision
from app.schemas.cases import CreateActionRequest
from app.models.entities import utcnow


class ActionEligibilityError(Exception):
    pass


class ActionIdempotencyConflict(Exception):
    pass


class InvalidActionTransition(Exception):
    pass


ALLOWED_TRANSITIONS = {
    "PENDING_APPROVAL": {"APPROVED", "REJECTED"},
    "APPROVED": {"EXECUTING"},
    "EXECUTING": {"SUCCEEDED", "FAILED"},
    "REJECTED": set(),
    "SUCCEEDED": set(),
    "FAILED": set(),
}


def _audit(session, case, action, details):
    session.add(AuditEvent(
        event_id=case.event_id,
        action=action,
        details=dict(details, case_id=case.id),
    ))


def create_proposed_action(session: Session, case: DisruptionCase, request: CreateActionRequest):
    if request.action_type == "REBOOK" and (not request.inventory_id or request.message is not None):
        raise ActionEligibilityError("Rebooking proposals require an alternative and do not accept a message")
    if request.action_type == "REFUND_REVIEW" and (
        request.inventory_id is not None or request.message is not None
    ):
        raise ActionEligibilityError("Refund-review proposals do not accept an alternative or message")
    if request.action_type == "SEND_MESSAGE" and request.inventory_id is not None:
        raise ActionEligibilityError("Message proposals do not accept an alternative")
    existing = session.scalar(
        select(RecoveryAction).where(RecoveryAction.idempotency_key == request.idempotency_key)
    )
    if existing is not None:
        proposed_candidate = (existing.proposed_payload.get("alternative") or {}).get("inventory_id")
        same_request = (
            existing.case_id == case.id
            and existing.action_type == request.action_type
            and (request.decision_id is None or existing.decision_id == request.decision_id)
            and (
                (request.action_type == "REBOOK" and proposed_candidate == request.inventory_id)
                or (request.action_type == "SEND_MESSAGE"
                    and existing.proposed_payload.get("message") == request.message)
                or request.action_type == "REFUND_REVIEW"
            )
        )
        if not same_request:
            raise ActionIdempotencyConflict("Idempotency key was already used for a different action")
        return existing, False

    decision = None
    if request.decision_id is not None:
        decision = session.get(RecoveryDecision, request.decision_id)
        if decision is None or decision.case_id != case.id:
            raise ActionEligibilityError("Recovery decision not found for this case")
    else:
        decision = session.scalar(
            select(RecoveryDecision)
            .where(RecoveryDecision.case_id == case.id)
            .order_by(RecoveryDecision.evaluated_at.desc(), RecoveryDecision.id.desc())
        )
    if decision is None:
        raise ActionEligibilityError("Evaluate this case before proposing an action")

    payload = {"simulated": True}
    if request.action_type == "REBOOK":
        if decision.decision_code != "recommend_for_review":
            raise ActionEligibilityError("A rebooking proposal requires an eligible recovery recommendation")
        candidate = next((
            candidate for candidate in decision.candidates
            if candidate.get("inventory_id") == request.inventory_id and candidate.get("simulated") is True
        ), None)
        if candidate is None:
            raise ActionEligibilityError("Select a simulated alternative from this decision")
        payload["alternative"] = candidate
    elif request.action_type == "REFUND_REVIEW":
        if decision.decision_code not in ("manual_review", "escalate"):
            raise ActionEligibilityError("Refund review proposals are only available for manual-review or escalated cases")
        payload.update({"reason_codes": decision.reason_codes, "review_only": True})
    elif request.action_type == "SEND_MESSAGE":
        if not request.message:
            raise ActionEligibilityError("A generated passenger-message draft is required")
        drafts = session.scalars(
            select(AuditEvent).where(
                AuditEvent.event_id == case.event_id,
                AuditEvent.action == "message_draft_generated",
            ).order_by(AuditEvent.id.desc())
        ).all()
        matching_draft = next((draft for draft in drafts if draft.details.get("case_id") == case.id), None)
        if (
            matching_draft is None
            or matching_draft.details.get("decision_id") != decision.id
            or matching_draft.details.get("draft_message") != request.message
        ):
            raise ActionEligibilityError("Message must match a saved draft for the selected decision")
        payload["message"] = request.message
        payload["draft_only"] = True

    recovery_action = RecoveryAction(
        action_id=uuid4().hex,
        case_id=case.id,
        decision_id=decision.id,
        action_type=request.action_type,
        proposed_payload=payload,
        state="PENDING_APPROVAL",
        idempotency_key=request.idempotency_key,
    )
    session.add(recovery_action)
    session.flush()
    _audit(session, case, "action_proposed", {
        "action_id": recovery_action.action_id,
        "action_type": recovery_action.action_type,
        "decision_id": decision.id,
        "policy_version": decision.policy_version,
    })
    session.flush()
    return recovery_action, True


def reject_action(session: Session, action_id: str, reason: str = None):
    _acquire_sqlite_write_lock(session)
    action = session.get(RecoveryAction, action_id)
    if action is None:
        return None
    case = session.get(DisruptionCase, action.case_id)
    _transition(session, action, case, "REJECTED", {"PENDING_APPROVAL"}, "action_rejected", {
        "reason": reason,
    })
    return action


def approve_and_execute(session: Session, action_id: str, approved_by: str):
    _acquire_sqlite_write_lock(session)
    action = session.get(RecoveryAction, action_id)
    if action is None:
        return None
    case = session.get(DisruptionCase, action.case_id)
    now = utcnow()
    _transition(session, action, case, "APPROVED", {"PENDING_APPROVAL"}, "action_approved", {
        "approved_by": approved_by,
    }, values={"approved_at": now, "approved_by": approved_by})
    _transition(session, action, case, "EXECUTING", {"APPROVED"}, "action_execution_started", {})

    try:
        succeeded, result = execute_simulated_action(action)
    except Exception as error:
        succeeded, result = False, {
            "simulated": True,
            "real_world_completed": False,
            "failure_reason": "SIMULATED_EXECUTOR_ERROR",
            "error_type": type(error).__name__,
        }
    terminal_state = "SUCCEEDED" if succeeded else "FAILED"
    failure_reason = None if succeeded else result.get("failure_reason", "Simulated execution failed")
    _transition(session, action, case, terminal_state, {"EXECUTING"},
                "action_succeeded" if succeeded else "action_failed", {
                    "result": result,
                    "simulated": True,
                }, values={
                    "executed_at": utcnow(),
                    "execution_result": result,
                    "failure_reason": failure_reason,
                })
    return action


def _transition(session, action, case, target, expected, audit_action, details, values=None):
    if action.state not in expected or target not in ALLOWED_TRANSITIONS.get(action.state, set()):
        _audit(session, case, "action_transition_rejected", {
            "action_id": action.action_id,
            "current_state": action.state,
            "requested_state": target,
        })
        session.flush()
        raise InvalidActionTransition(
            "Cannot transition action from {} to {}".format(action.state, target)
        )
    updates = dict(values or {})
    updates["state"] = target
    result = session.execute(
        update(RecoveryAction)
        .where(RecoveryAction.action_id == action.action_id, RecoveryAction.state == action.state)
        .values(**updates)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        session.refresh(action)
        _audit(session, case, "action_transition_rejected", {
            "action_id": action.action_id,
            "current_state": action.state,
            "requested_state": target,
        })
        session.flush()
        raise InvalidActionTransition("Action state changed before transition could be applied")
    action.state = target
    for key, value in (values or {}).items():
        setattr(action, key, value)
    _audit(session, case, audit_action, dict({"action_id": action.action_id}, **details))
    session.flush()


def execute_simulated_action(action: RecoveryAction):
    """Perform only deterministic local bookkeeping; never contact a downstream service."""
    if action.proposed_payload.get("_simulate_failure") is True:
        return False, {
            "simulated": True,
            "real_world_completed": False,
            "failure_reason": "SIMULATED_DOWNSTREAM_FAILURE",
        }
    if action.action_type == "REBOOK":
        alternative = action.proposed_payload["alternative"]
        return True, {
            "simulated": True,
            "outcome": "simulated_rebooking_recorded",
            "inventory_id": alternative["inventory_id"],
            "flight_id": alternative["flight_id"],
            "real_world_completed": False,
        }
    if action.action_type == "REFUND_REVIEW":
        return True, {
            "simulated": True,
            "outcome": "simulated_refund_review_created",
            "money_refunded": False,
            "real_world_completed": False,
        }
    if action.action_type == "SEND_MESSAGE":
        return True, {
            "simulated": True,
            "outcome": "simulated_message_delivery_recorded",
            "real_message_sent": False,
            "real_world_completed": False,
            "message_length": len(action.proposed_payload["message"]),
        }
    return False, {
        "simulated": True,
        "real_world_completed": False,
        "failure_reason": "UNSUPPORTED_ACTION_TYPE",
    }


def _acquire_sqlite_write_lock(session: Session):
    """Serialize local SQLite action transitions before reading state."""
    connection = session.connection()
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql("BEGIN IMMEDIATE")
