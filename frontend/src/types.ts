export interface HealthResponse {
  status: string
}

export interface CaseListItem {
  id: number
  booking_ref: string
  passenger_name: string
  flight_id: string
  status: string
  created_at: string
}

export interface ItineraryLeg {
  sequence: number
  flight_id: string
  airline_name?: string | null
  origin?: string | null
  destination?: string | null
  scheduled_departure?: string | null
  scheduled_arrival?: string | null
  departure_timezone?: string | null
  arrival_timezone?: string | null
}

export interface ConnectionAssessment {
  first_flight_id: string
  next_flight_id: string
  status: string
  available_minutes?: number | null
  minimum_minutes: number
  reason_codes: string[]
}

export interface CandidateAlternative {
  inventory_id: string
  flight_id: string
  origin: string
  destination: string
  scheduled_departure: string
  scheduled_arrival: string
  simulated: boolean
  score: number
  ranking_factors: string[]
}

export interface RecoveryEvaluation {
  case_id: number
  decision_id: number
  decision_code: 'recommend_for_review' | 'escalate' | 'manual_review' | string
  explanation: string
  reason_codes: string[]
  policy_id: string
  policy_version: string
  requires_human_approval: boolean
  simulated_inventory: boolean
  connection_assessments: ConnectionAssessment[]
  candidates: CandidateAlternative[]
  side_effects_executed: boolean
  evaluated_at: string
}

export interface AuditTimelineItem {
  at: string
  action: string
  details: Record<string, unknown>
}

export interface CaseDetail extends CaseListItem {
  event_id: string
  disruption_reason?: string | null
  itinerary: ItineraryLeg[]
  passenger_constraints: Record<string, unknown>
  audit_timeline: AuditTimelineItem[]
  latest_decision: RecoveryEvaluation | null
}

export type ActionType = 'REBOOK' | 'REFUND_REVIEW' | 'SEND_MESSAGE'
export type ActionState =
  | 'PENDING_APPROVAL'
  | 'APPROVED'
  | 'REJECTED'
  | 'EXECUTING'
  | 'SUCCEEDED'
  | 'FAILED'

export interface RecoveryAction {
  action_id: string
  case_id: number
  decision_id: number
  action_type: ActionType
  proposed_payload: Record<string, unknown>
  state: ActionState
  idempotency_key: string
  created_at: string
  approved_at: string | null
  executed_at: string | null
  approved_by: string | null
  execution_result: Record<string, unknown> | null
  failure_reason: string | null
}

export interface CopilotRecommendation {
  decision_id: number
  decision_code: string
  explanation: string
  reason_codes: string[]
  policy_id: string
  policy_version: string
}

export interface CopilotMetadata {
  provider: 'gemini' | 'deterministic_fallback' | string
  model: string | null
  fallback_used: boolean
  validation_status: string
}

export type AgentToolName =
  | 'search_synthetic_alternatives'
  | 'check_connection_feasibility'
  | 'compare_recovery_options'
  | 'propose_recovery_action'

export interface AgentToolResult {
  name: AgentToolName
  status: 'completed' | 'blocked' | 'invalid'
  result: Record<string, unknown>
}

export interface AgentRunResponse {
  case_id: number
  summary: string
  tool_results: AgentToolResult[]
  proposed_action_id: string | null
  approval_required: boolean
  simulated: true
  metadata: CopilotMetadata
}

export interface EvidenceStatement {
  text: string
  evidence_refs: string[]
}

export interface CopilotAnalysisResponse {
  case_id: number
  analysis_status: 'complete' | 'incomplete' | string
  disruption_summary: string
  confirmed_facts: EvidenceStatement[]
  risk_findings: EvidenceStatement[]
  missing_information: string[]
  recommendation_explanation: string
  trade_offs: EvidenceStatement[]
  recommended_next_step: string
  evidence_refs: string[]
  metadata: CopilotMetadata
}

export interface CopilotAskResponse {
  case_id: number
  answer: string
  evidence_refs: string[]
  missing_information: string[]
  metadata: CopilotMetadata
}

export interface CaseCopilotResponse {
  case_id: number
  case_summary: string
  recommendation: CopilotRecommendation
  warnings: string[]
  draft_message: string | null
  draft_only: true
  sent: false
  provider: string
  model: string | null
  fallback_used: boolean
  validation_status: string
  evidence_refs: string[]
  metadata: CopilotMetadata | null
}

export interface FlightAlertRequest {
  event_id: string
  fid: string
  changes: { status: 'cancel' }
}

export interface FlightAlertResponse {
  event_id: string
  status: string
  duplicate: boolean
  cases_created: number
}
