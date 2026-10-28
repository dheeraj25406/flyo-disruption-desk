import type {
  AgentRunResponse,
  CaseCopilotResponse,
  CaseDetail,
  CaseListItem,
  CopilotAnalysisResponse,
  CopilotMetadata,
  CopilotAskResponse,
  FlightAlertRequest,
  FlightAlertResponse,
  HealthResponse,
  RecoveryAction,
  RecoveryEvaluation,
} from './types'

const apiBase = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '')

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isMetadata(value: unknown): value is CopilotMetadata {
  return isRecord(value) && typeof value.provider === 'string' &&
    typeof value.fallback_used === 'boolean' && typeof value.validation_status === 'string'
}

function checked<T>(value: unknown, valid: boolean, label: string): T {
  if (!valid) throw new ApiError('The API returned an invalid ' + label + '.', 502)
  return value as T
}

async function request<T>(
  path: string,
  init?: RequestInit,
  onResponse?: (response: Response) => void,
): Promise<T> {
  let response: Response
  try {
    response = await fetch(apiBase + path, {
      ...init,
      headers: {
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        ...init?.headers,
      },
    })
  } catch {
    throw new ApiError('Could not reach the Flyo API. Check that the backend is running.', 0)
  }
  onResponse?.(response)

  const text = await response.text()
  let data: unknown = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      throw new ApiError('The API returned an unreadable response.', response.status)
    }
  }
  if (!response.ok) {
    const detail =
      typeof data === 'object' && data !== null && 'detail' in data
        ? String((data as { detail: unknown }).detail)
        : 'Request failed with status ' + response.status + '.'
    throw new ApiError(detail, response.status)
  }
  return data as T
}

function newTraceId(): string {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (character) => {
    const random = Math.random() * 16 | 0
    return (character === 'x' ? random : (random & 0x3 | 0x8)).toString(16)
  })
}

function logTrace(fields: {
  operation: string
  trace_id: string
  stage: string
  http_status: number | null
  exception_class: string | null
  category: string
}): void {
  try {
    console.info('[flyo-trace]', fields)
  } catch {
    // Diagnostics must not interrupt an API request.
  }
}

function responseCategory(value: unknown): string {
  if (isRecord(value)) {
    const metadata = value.metadata
    if (isMetadata(metadata)) {
      return metadata.fallback_used ? metadata.validation_status : 'validated'
    }
  }
  return 'response_received'
}

async function tracedRequest<T>(
  operation: 'analyze' | 'agent',
  path: string,
  init: RequestInit,
  validate: (value: unknown) => T,
): Promise<T> {
  const traceId = newTraceId()
  let httpStatus: number | null = null
  logTrace({
    operation,
    trace_id: traceId,
    stage: 'request_start',
    http_status: null,
    exception_class: null,
    category: 'none',
  })
  try {
    const result: unknown = await request(path, {
      ...init,
      headers: { 'X-Request-ID': traceId },
    }, (response) => { httpStatus = response.status })
    const parsed = validate(result)
    logTrace({
      operation,
      trace_id: traceId,
      stage: 'request_end',
      http_status: httpStatus,
      exception_class: null,
      category: responseCategory(parsed),
    })
    return parsed
  } catch (error) {
    const status = httpStatus ?? (error instanceof ApiError ? error.status : null)
    const errorName = error instanceof Error ? error.name : 'UnknownError'
    logTrace({
      operation,
      trace_id: traceId,
      stage: 'request_end',
      http_status: status,
      exception_class: /^[A-Za-z0-9_-]{1,64}$/.test(errorName) ? errorName : 'Error',
      category: status === 0 ? 'network_error' : status !== null && status >= 400
        ? 'http_or_response_error'
        : 'request_error',
    })
    throw error
  }
}

async function actionResponse(path: string, init?: RequestInit): Promise<RecoveryAction> {
  const result: unknown = await request(path, init)
  return checked<RecoveryAction>(
    result,
    isRecord(result) && typeof result.action_id === 'string' && typeof result.state === 'string',
    'action',
  )
}

function isCaseList(value: unknown): value is CaseListItem[] {
  return (
    Array.isArray(value) &&
    value.every(
      (item) =>
        typeof item === 'object' &&
        item !== null &&
        typeof (item as CaseListItem).id === 'number' &&
        typeof (item as CaseListItem).flight_id === 'string',
    )
  )
}

export const api = {
  async health(): Promise<HealthResponse> {
    const result: unknown = await request('/health')
    return checked<HealthResponse>(result, isRecord(result) && typeof result.status === 'string', 'health response')
  },
  async cases(): Promise<CaseListItem[]> {
    const result: unknown = await request('/cases')
    if (!isCaseList(result)) throw new ApiError('The API returned an invalid case list.', 502)
    return result
  },
  async caseDetail(caseId: number): Promise<CaseDetail> {
    const result: unknown = await request('/cases/' + caseId)
    const valid = isRecord(result) &&
      typeof result.id === 'number' &&
      typeof result.flight_id === 'string' &&
      Array.isArray(result.itinerary) &&
      Array.isArray(result.audit_timeline) &&
      (result.latest_decision === null || isRecord(result.latest_decision))
    return checked<CaseDetail>(result, valid, 'case detail')
  },
  async evaluate(caseId: number): Promise<RecoveryEvaluation> {
    const result: unknown = await request('/cases/' + caseId + '/evaluate', { method: 'POST' })
    const valid = isRecord(result) &&
      typeof result.decision_id === 'number' &&
      typeof result.decision_code === 'string' &&
      Array.isArray(result.reason_codes) &&
      Array.isArray(result.connection_assessments) &&
      Array.isArray(result.candidates)
    return checked<RecoveryEvaluation>(result, valid, 'recovery evaluation')
  },
  proposeAction: (
    caseId: number,
    body: {
      action_type: 'REBOOK' | 'REFUND_REVIEW' | 'SEND_MESSAGE'
      idempotency_key: string
      decision_id: number
      inventory_id?: string
      message?: string
    },
  ) =>
    actionResponse('/cases/' + caseId + '/actions', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  async action(actionId: string): Promise<RecoveryAction> {
    const result: unknown = await request('/actions/' + actionId)
    return checked<RecoveryAction>(
      result,
      isRecord(result) && typeof result.action_id === 'string' && typeof result.state === 'string',
      'action',
    )
  },
  approve: (actionId: string) =>
    actionResponse('/actions/' + actionId + '/approve', {
      method: 'POST',
      body: JSON.stringify({ approved: true, approved_by: 'demo-operator' }),
    }),
  reject: (actionId: string, reason: string) =>
    actionResponse('/actions/' + actionId + '/reject', {
      method: 'POST',
      body: JSON.stringify({ reason }),
    }),
  async draftMessage(caseId: number): Promise<CaseCopilotResponse> {
    const result: unknown = await request('/cases/' + caseId + '/draft-message', { method: 'POST' })
    const valid = isRecord(result) &&
      typeof result.case_summary === 'string' &&
      isRecord(result.recommendation) &&
      result.draft_only === true &&
      result.sent === false &&
      typeof result.provider === 'string' &&
      isMetadata(result.metadata)
    return checked<CaseCopilotResponse>(result, valid, 'copilot response')
  },
  async analyzeCopilot(caseId: number): Promise<CopilotAnalysisResponse> {
    return tracedRequest('analyze', '/cases/' + caseId + '/copilot/analyze', { method: 'POST' }, (result) => {
      const valid = isRecord(result) && typeof result.case_id === 'number' &&
        typeof result.disruption_summary === 'string' && Array.isArray(result.confirmed_facts) &&
        Array.isArray(result.risk_findings) && Array.isArray(result.trade_offs) &&
        Array.isArray(result.evidence_refs) && isMetadata(result.metadata)
      return checked<CopilotAnalysisResponse>(result, valid, 'copilot analysis')
    })
  },
  async runAgent(caseId: number, objective: string): Promise<AgentRunResponse> {
    return tracedRequest('agent', '/cases/' + caseId + '/agent/run', {
      method: 'POST',
      body: JSON.stringify({ objective, allow_proposal: false }),
    }, (result) => {
      const toolNames = [
        'search_synthetic_alternatives',
        'check_connection_feasibility',
        'compare_recovery_options',
        'propose_recovery_action',
      ]
      const validToolResults = (value: unknown): boolean =>
        Array.isArray(value) && value.length <= 4 && value.every((item) =>
          isRecord(item) && typeof item.name === 'string' && toolNames.includes(item.name) &&
          ['completed', 'blocked', 'invalid'].includes(String(item.status)) && isRecord(item.result),
        )
      const valid = isRecord(result) &&
        typeof result.case_id === 'number' &&
        typeof result.summary === 'string' &&
        validToolResults(result.tool_results) &&
        (typeof result.proposed_action_id === 'string' || result.proposed_action_id === null) &&
        typeof result.approval_required === 'boolean' &&
        result.simulated === true &&
        isMetadata(result.metadata)
      return checked<AgentRunResponse>(result, valid, 'agent response')
    })
  },
  async askCopilot(caseId: number, question: string): Promise<CopilotAskResponse> {
    const result: unknown = await request('/cases/' + caseId + '/copilot/ask', {
      method: 'POST', body: JSON.stringify({ question }),
    })
    const valid = isRecord(result) && typeof result.case_id === 'number' &&
      typeof result.answer === 'string' && Array.isArray(result.evidence_refs) &&
      Array.isArray(result.missing_information) && isMetadata(result.metadata)
    return checked<CopilotAskResponse>(result, valid, 'copilot answer')
  },
  async flightAlert(body: FlightAlertRequest): Promise<FlightAlertResponse> {
    const result: unknown = await request('/webhooks/flight-alerts', {
      method: 'POST',
      body: JSON.stringify(body),
    })
    return checked<FlightAlertResponse>(
      result,
      isRecord(result) && typeof result.event_id === 'string' && typeof result.cases_created === 'number',
      'flight-alert response',
    )
  },
}
