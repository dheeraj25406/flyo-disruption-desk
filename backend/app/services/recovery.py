from datetime import datetime
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent, Booking, DisruptionCase, RecoveryDecision
from app.schemas.cases import CandidateAlternative
from app.schemas.itinerary import ItineraryLeg
from app.services.policy import Alternative, DEFAULT_POLICY, SYNTHETIC_INVENTORY, RecoveryPolicy
from app.services.risk import RISKY_STATUSES, assess_connections, parse_zoned_instant


def evaluate_case(
    session: Session,
    case: DisruptionCase,
    booking: Booking,
    policy: RecoveryPolicy = DEFAULT_POLICY,
    inventory: Iterable[Alternative] = SYNTHETIC_INVENTORY,
) -> RecoveryDecision:
    try:
        legs = [ItineraryLeg.model_validate(item) for item in (booking.itinerary or [])]
    except (TypeError, ValueError):
        legs = []
    legs.sort(key=lambda leg: leg.sequence)
    connections = assess_connections(legs, policy)
    reason_codes = []
    target = next((leg for leg in legs if leg.flight_id == case.flight_id), None)
    if target is None:
        reason_codes.append("disrupted_flight_missing_from_itinerary")
    elif (
        not target.origin or not target.destination
        or parse_zoned_instant(target.scheduled_departure, target.departure_timezone) is None
        or parse_zoned_instant(target.scheduled_arrival, target.arrival_timezone) is None
    ):
        reason_codes.append("disrupted_leg_schedule_or_timezone_missing")
    if not legs:
        reason_codes.append("itinerary_missing_or_invalid")
    for connection in connections:
        if connection.status in RISKY_STATUSES:
            reason_codes.extend(connection.reason_codes)
            reason_codes.append("itinerary_connection_requires_review")

    candidate_results = []
    rejection_codes = []
    if not reason_codes and target is not None:
        for alternative in inventory:
            candidate, rejected = _evaluate_alternative(
                alternative, target, legs, booking.passenger_constraints or {}, policy
            )
            if candidate is not None:
                candidate_results.append(candidate)
            else:
                rejection_codes.extend(rejected)
    candidate_results.sort(key=lambda item: (-item.score, item.inventory_id))

    if reason_codes:
        decision_code = "escalate"
        explanation = "The itinerary has missing or risky information and needs human review before recovery can be considered."
        reason_codes = list(dict.fromkeys(reason_codes))
        candidates = []
    elif candidate_results:
        decision_code = "recommend_for_review"
        explanation = "A simulated alternative meets the configured route, timing, passenger, and connection constraints. Human approval is required; no action was taken."
        reason_codes = ["eligible_simulated_alternative_available"]
        candidates = [item.model_dump(mode="json") for item in candidate_results]
    else:
        decision_code = "manual_review"
        explanation = "No simulated alternative met all configured hard constraints, so this case requires manual review."
        reason_codes = ["no_eligible_simulated_alternative"] + list(dict.fromkeys(rejection_codes))
        candidates = []

    evidence = {
        "affected_flight_id": case.flight_id,
        "connection_assessments": [item.model_dump(mode="json") for item in connections],
        "rejected_candidate_reasons": list(dict.fromkeys(rejection_codes)),
        "constraints": booking.passenger_constraints or {},
        "inventory_is_simulated": True,
    }
    decision = RecoveryDecision(
        case_id=case.id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        decision_code=decision_code,
        explanation=explanation,
        reason_codes=reason_codes,
        requires_human_approval=True,
        candidates=candidates,
        evidence=evidence,
    )
    session.add(decision)
    session.flush()
    session.add(AuditEvent(
        event_id=case.event_id,
        action="case_evaluated",
        details={
            "case_id": case.id,
            "decision_id": decision.id,
            "decision_code": decision_code,
            "policy_id": policy.policy_id,
            "policy_version": policy.version,
        },
    ))
    session.flush()
    return decision


def _evaluate_alternative(alternative, target, legs, constraints, policy):
    if not alternative.available:
        return None, ["simulated_inventory_unavailable"]
    if (alternative.origin, alternative.destination) != (target.origin, target.destination):
        return None, ["alternative_route_incompatible"]

    original_departure = parse_zoned_instant(target.scheduled_departure, target.departure_timezone)
    original_arrival = parse_zoned_instant(target.scheduled_arrival, target.arrival_timezone)
    candidate_departure = parse_zoned_instant(alternative.scheduled_departure, alternative.departure_timezone)
    candidate_arrival = parse_zoned_instant(alternative.scheduled_arrival, alternative.arrival_timezone)
    if not all((original_departure, original_arrival, candidate_departure, candidate_arrival)):
        return None, ["alternative_or_original_schedule_invalid"]
    if candidate_arrival <= candidate_departure:
        return None, ["alternative_schedule_impossible"]

    departure_shift = int((candidate_departure - original_departure).total_seconds() // 60)
    arrival_delay = int((candidate_arrival - original_arrival).total_seconds() // 60)
    max_shift = int(constraints.get("max_departure_shift_minutes", policy.max_departure_shift_minutes))
    max_arrival_delay = int(constraints.get("max_arrival_delay_minutes", policy.max_arrival_delay_minutes))
    if abs(departure_shift) > max_shift:
        return None, ["departure_shift_exceeds_passenger_constraint"]
    if arrival_delay > max_arrival_delay:
        return None, ["arrival_delay_exceeds_passenger_constraint"]
    must_arrive_by = constraints.get("must_arrive_by")
    if must_arrive_by:
        try:
            deadline = datetime.fromisoformat(must_arrive_by.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            return None, ["passenger_arrival_deadline_invalid"]
        if deadline.tzinfo is None or candidate_arrival > deadline.astimezone(candidate_arrival.tzinfo):
            return None, ["arrival_after_passenger_deadline"]

    replacement = target.model_copy(update={
        "flight_id": alternative.flight_id,
        "origin": alternative.origin,
        "destination": alternative.destination,
        "scheduled_departure": alternative.scheduled_departure,
        "scheduled_arrival": alternative.scheduled_arrival,
        "departure_timezone": alternative.departure_timezone,
        "arrival_timezone": alternative.arrival_timezone,
    })
    replaced_legs = [replacement if leg.sequence == target.sequence else leg for leg in legs]
    simulated_connections = assess_connections(replaced_legs, policy)
    if any(connection.status in RISKY_STATUSES for connection in simulated_connections):
        return None, ["alternative_connection_not_feasible"]

    departure_cost = abs(departure_shift) * policy.departure_shift_weight
    arrival_cost = max(0, arrival_delay) * policy.arrival_delay_weight
    score = max(0, 100 - (departure_cost + arrival_cost) // 10)
    factors = [
        "score = max(0, 100 - floor((departure shift cost + positive arrival delay cost) / 10))",
        "departure shift: {} min × weight {}".format(departure_shift, policy.departure_shift_weight),
        "arrival delay: {} min × weight {}".format(max(0, arrival_delay), policy.arrival_delay_weight),
        "all hard route, availability, passenger, and connection constraints passed",
    ]
    return CandidateAlternative(
        inventory_id=alternative.inventory_id,
        flight_id=alternative.flight_id,
        origin=alternative.origin,
        destination=alternative.destination,
        scheduled_departure=alternative.scheduled_departure,
        scheduled_arrival=alternative.scheduled_arrival,
        simulated=True,
        score=score,
        ranking_factors=factors,
    ), []
