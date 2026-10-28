"""Typed, bounded schemas for advisory copilot outputs."""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class CopilotMetadata(BaseModel):
    provider: str
    model: Optional[str] = None
    fallback_used: bool
    validation_status: str


class EvidenceStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=500)
    evidence_refs: List[str] = Field(default_factory=list, max_length=8)


class AnalysisPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disruption_summary: str = Field(min_length=1, max_length=700)
    confirmed_facts: List[EvidenceStatement] = Field(default_factory=list, max_length=8)
    risk_findings: List[EvidenceStatement] = Field(default_factory=list, max_length=8)
    missing_information: List[str] = Field(default_factory=list, max_length=8)
    recommendation_explanation: str = Field(min_length=1, max_length=700)
    trade_offs: List[EvidenceStatement] = Field(default_factory=list, max_length=8)
    recommended_next_step: str = Field(min_length=1, max_length=400)
    evidence_refs: List[str] = Field(default_factory=list, max_length=16)


class CopilotAnalysisResponse(AnalysisPayload):
    case_id: int
    analysis_status: str
    metadata: CopilotMetadata


class AskCopilotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1000)


class AskPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=1200)
    evidence_refs: List[str] = Field(default_factory=list, max_length=12)
    missing_information: List[str] = Field(default_factory=list, max_length=8)


class CopilotAskResponse(AskPayload):
    case_id: int
    metadata: CopilotMetadata


class DraftPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=1800)
    evidence_refs: List[str] = Field(default_factory=list, max_length=8)
    warnings: List[str] = Field(default_factory=list, max_length=8)


AgentToolName = Literal[
    "search_synthetic_alternatives",
    "check_connection_feasibility",
    "compare_recovery_options",
    "propose_recovery_action",
]


class AgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    objective: str = Field(min_length=1, max_length=1000)
    allow_proposal: bool = False


class AgentToolResult(BaseModel):
    name: AgentToolName
    status: Literal["completed", "blocked", "invalid"]
    result: dict


class AgentRunResponse(BaseModel):
    case_id: int
    summary: str = Field(min_length=1, max_length=1200)
    tool_results: List[AgentToolResult] = Field(default_factory=list, max_length=4)
    proposed_action_id: Optional[str] = None
    approval_required: bool = True
    simulated: Literal[True] = True
    metadata: CopilotMetadata
