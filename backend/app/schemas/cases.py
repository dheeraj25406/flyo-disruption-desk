from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.copilot import CopilotMetadata
from app.schemas.itinerary import ConnectionAssessment, ItineraryLeg


class CaseListItem(BaseModel):
    id: int
    booking_ref: str
    passenger_name: str
    flight_id: str
    status: str
    created_at: datetime


class CaseDetail(CaseListItem):
    event_id: str
    itinerary: list[ItineraryLeg]
    passenger_constraints: dict
    audit_timeline: list["AuditTimelineItem"] = Field(default_factory=list)
    latest_decision: Optional["RecoveryEvaluation"] = None


class AuditTimelineItem(BaseModel):
    at: datetime
    action: str
    details: dict


class CandidateAlternative(BaseModel):
    inventory_id: str
    flight_id: str
    origin: str
    destination: str
    scheduled_departure: str
    scheduled_arrival: str
    simulated: bool = True
    score: int
    ranking_factors: list[str]


class RecoveryEvaluation(BaseModel):
    case_id: int
    decision_id: int
    decision_code: str
    explanation: str
    reason_codes: list[str]
    policy_id: str
    policy_version: str
    requires_human_approval: bool
    simulated_inventory: bool = True
    connection_assessments: list[ConnectionAssessment]
    candidates: list[CandidateAlternative]
    side_effects_executed: bool = False
    evaluated_at: datetime


ActionType = Literal["REBOOK", "REFUND_REVIEW", "SEND_MESSAGE"]
ActionState = Literal[
    "PENDING_APPROVAL", "APPROVED", "REJECTED", "EXECUTING", "SUCCEEDED", "FAILED"
]


class CreateActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: ActionType
    idempotency_key: str = Field(min_length=1, max_length=128)
    decision_id: Optional[int] = None
    inventory_id: Optional[str] = Field(default=None, max_length=128)
    message: Optional[str] = Field(default=None, min_length=1, max_length=2000)


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: Literal[True]
    approved_by: str = Field(default="demo-operator", min_length=1, max_length=120)


class RejectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Optional[str] = Field(default=None, max_length=500)


class RecoveryActionResponse(BaseModel):
    action_id: str
    case_id: int
    decision_id: int
    action_type: ActionType
    proposed_payload: dict
    state: ActionState
    idempotency_key: str
    created_at: datetime
    approved_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    execution_result: Optional[dict] = None
    failure_reason: Optional[str] = None


class CopilotRecommendation(BaseModel):
    decision_id: int
    decision_code: str
    explanation: str
    reason_codes: list[str]
    policy_id: str
    policy_version: str


class CaseCopilotResponse(BaseModel):
    case_id: int
    case_summary: str
    recommendation: CopilotRecommendation
    warnings: list[str]
    draft_message: Optional[str]
    draft_only: Literal[True] = True
    sent: Literal[False] = False
    provider: str
    model: Optional[str] = None
    fallback_used: bool = True
    validation_status: str = "validated"
    metadata: Optional[CopilotMetadata] = None
    evidence_refs: list[str] = Field(default_factory=list)
