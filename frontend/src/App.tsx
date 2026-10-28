import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { api } from './api'
import { matchesCaseSearch } from './caseSearch.js'
import type {
  ActionType, AgentRunResponse, AuditTimelineItem, CandidateAlternative, CaseCopilotResponse,
  CaseDetail, CaseListItem, CopilotAnalysisResponse, CopilotAskResponse,
  CopilotMetadata, RecoveryAction,
} from './types'
import './App.css'

type View = 'operations' | 'cases' | 'activity'
type IconName = 'grid' | 'cases' | 'activity' | 'plane' | 'search' | 'chevron' | 'plus' | 'refresh' | 'arrow' | 'check' | 'alert' | 'clock' | 'calendar' | 'route' | 'spark' | 'close' | 'send' | 'shield' | 'filter'

function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  const shapes: Record<IconName, ReactNode> = {
    grid: <><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></>,
    cases: <><path d="M3.5 7.5h17v12a1.5 1.5 0 0 1-1.5 1.5H5a1.5 1.5 0 0 1-1.5-1.5z" /><path d="M8 7.5V5.8A1.8 1.8 0 0 1 9.8 4h4.4A1.8 1.8 0 0 1 16 5.8v1.7M3.5 12h17" /></>,
    activity: <><path d="M3 12h4l2.4-7 4.2 14 2.4-7H21" /></>,
    plane: <><path d="m21 3-7.2 18-3.3-7.5L3 10.2 21 3Z" /><path d="m10.5 13.5 4.2-4.2" /></>,
    search: <><circle cx="10.8" cy="10.8" r="6.8" /><path d="m16 16 4.5 4.5" /></>,
    chevron: <path d="m9 18 6-6-6-6" />,
    plus: <><path d="M12 5v14M5 12h14" /></>,
    refresh: <><path d="M20 7v5h-5M4 17v-5h5" /><path d="M5.5 9A7 7 0 0 1 18 6l2 2M4 16l2 2a7 7 0 0 0 12.5-3" /></>,
    arrow: <><path d="M5 12h14M13 6l6 6-6 6" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    alert: <><path d="M10.3 4.2 2.8 17.5A1.7 1.7 0 0 0 4.3 20h15.4a1.7 1.7 0 0 0 1.5-2.5L13.7 4.2a2 2 0 0 0-3.4 0Z" /><path d="M12 9v4m0 3h.01" /></>,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    calendar: <><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M16 3v4M8 3v4M3 10h18" /></>,
    route: <><circle cx="6" cy="6" r="2" /><circle cx="18" cy="18" r="2" /><path d="M8 6h4a3 3 0 0 1 3 3v2a3 3 0 0 0 3 3" /></>,
    spark: <><path d="m12 3 1.7 5.3L19 10l-5.3 1.7L12 17l-1.7-5.3L5 10l5.3-1.7L12 3Z" /><path d="m19 15 .8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8L19 15Z" /></>,
    close: <><path d="m6 6 12 12M18 6 6 18" /></>,
    send: <><path d="m22 2-7 20-4-9-9-4 20-7ZM22 2 11 13" /></>,
    shield: <><path d="M12 22s8-4 8-11V5l-8-3-8 3v6c0 7 8 11 8 11Z" /><path d="m9 12 2 2 4-4" /></>,
    filter: <><path d="M4 6h16M7 12h10m-7 6h4" /></>,
  }
  return <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">{shapes[name]}</svg>
}

const errorText = (error: unknown) => error instanceof Error ? error.message : 'Something went wrong. Please try again.'
const decisionText = (code?: string | null) => code === 'recommend_for_review' ? 'Recommendation ready' : code === 'escalate' ? 'Escalated' : code === 'manual_review' ? 'Manual review' : 'Not evaluated'
const decisionTone = (code?: string | null) => code === 'recommend_for_review' ? 'green' : code === 'escalate' ? 'amber' : code === 'manual_review' ? 'slate' : 'muted'
const actionTone = (state: string) => state === 'SUCCEEDED' ? 'green' : ['FAILED', 'REJECTED'].includes(state) ? 'red' : state === 'PENDING_APPROVAL' ? 'amber' : 'blue'
const pretty = (value?: string | null) => value ? value.replaceAll('_', ' ') : 'Unavailable'

function dateLabel(value?: string | null, opts?: Intl.DateTimeFormatOptions) {
  if (!value) return 'Not provided'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Schedule unavailable'
  return date.toLocaleString(undefined, opts || { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

function dateInZone(value?: string | null, zone?: string | null) {
  if (!value || !zone) return dateLabel(value)
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Schedule unavailable'
  try {
    return new Intl.DateTimeFormat(undefined, {
      month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
      timeZone: zone, timeZoneName: 'short',
    }).format(date)
  } catch {
    return dateLabel(value)
  }
}

function utcLabel(value?: string | null) {
  if (!value) return 'Schedule unavailable'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Schedule unavailable'
  return new Intl.DateTimeFormat(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
    timeZone: 'UTC', timeZoneName: 'short',
  }).format(date)
}

function Badge({ children, tone = 'slate' }: { children: ReactNode; tone?: string }) {
  return <span className={'badge badge-' + tone}>{children}</span>
}

function App() {
  const [view, setView] = useState<View>('operations')
  const [cases, setCases] = useState<CaseListItem[]>([])
  const [details, setDetails] = useState<Record<number, CaseDetail>>({})
  const [actions, setActions] = useState<Record<string, RecoveryAction>>({})
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [connected, setConnected] = useState<boolean | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const [demoOpen, setDemoOpen] = useState(false)
  const [confirm, setConfirm] = useState<{ kind: 'approve' | 'reject'; action: RecoveryAction } | null>(null)
  const [draft, setDraft] = useState('')
  const [savedDraft, setSavedDraft] = useState('')
  const [copilot, setCopilot] = useState<CaseCopilotResponse | null>(null)
  const [analysis, setAnalysis] = useState<CopilotAnalysisResponse | null>(null)
  const [agentRun, setAgentRun] = useState<AgentRunResponse | null>(null)
  const [copilotAnswer, setCopilotAnswer] = useState<CopilotAskResponse | null>(null)
  const [copilotQuestion, setCopilotQuestion] = useState('')
  const [actionType, setActionType] = useState<ActionType>('REBOOK')
  const [candidateId, setCandidateId] = useState('')

  const refreshData = useCallback(async () => {
    try {
      const apiStatus = await api.health().then(() => true).catch(() => false)
      setConnected(apiStatus)
      const nextCases = await api.cases()
      const detailResults = await Promise.allSettled(nextCases.map((item) => api.caseDetail(item.id)))
      const nextDetails: Record<number, CaseDetail> = {}
      detailResults.forEach((result, index) => {
        if (result.status === 'fulfilled') nextDetails[nextCases[index].id] = result.value
      })
      const actionIds = Array.from(new Set(Object.values(nextDetails).flatMap((detail) =>
        (detail.audit_timeline || [])
          .map((event) => event.details.action_id)
          .filter((id): id is string => typeof id === 'string'),
      )))
      const actionResults = await Promise.allSettled(actionIds.map((id) => api.action(id)))
      const nextActions: Record<string, RecoveryAction> = {}
      actionResults.forEach((result) => {
        if (result.status === 'fulfilled') nextActions[result.value.action_id] = result.value
      })
      setCases(nextCases)
      setDetails(nextDetails)
      setActions(nextActions)
      if (selectedId !== null && !nextCases.some((item) => item.id === selectedId)) setSelectedId(null)
      const selected = selectedId === null ? undefined : nextDetails[selectedId]
      const persistedDraft = selected?.audit_timeline.filter((item) => item.action === 'message_draft_generated').at(-1)?.details.draft_message
      if (typeof persistedDraft === 'string') {
        setDraft(persistedDraft)
        setSavedDraft(persistedDraft)
      } else if (selected) {
        setDraft('')
        setSavedDraft('')
      }
      setCopilot(null)
    } catch (loadError) {
      setError(errorText(loadError))
      setConnected(false)
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [selectedId])

  const refreshQuietly = async () => {
    setRefreshing(true)
    await refreshData()
  }

  useEffect(() => { void Promise.resolve().then(refreshData) }, [refreshData])

  const selectedCase = selectedId === null ? undefined : details[selectedId]
  const selectedActions = useMemo(() => Object.values(actions)
    .filter((action) => action.case_id === selectedId)
    .sort((a, b) => b.created_at.localeCompare(a.created_at)), [actions, selectedId])

  const openCase = (id: number) => {
    setSelectedId(id)
    setError('')
    setNotice('')
    const persistedDraft = details[id]?.audit_timeline.filter((item) => item.action === 'message_draft_generated').at(-1)?.details.draft_message
    setDraft(typeof persistedDraft === 'string' ? persistedDraft : '')
    setSavedDraft(typeof persistedDraft === 'string' ? persistedDraft : '')
    setCopilot(null)
    setAnalysis(null)
    setAgentRun(null)
    setCopilotAnswer(null)
    setCopilotQuestion('')
  }

  const mutate = async (key: string, callback: () => Promise<void>) => {
    setBusy(key)
    setError('')
    setNotice('')
    try { await callback() } catch (mutationError) { setError(errorText(mutationError)) } finally { setBusy('') }
  }
  const refreshSelected = async () => {
    setRefreshing(true)
    await refreshData()
  }

  const runEvaluation = () => {
    if (!selectedCase) return
    void mutate('evaluate', async () => {
      await api.evaluate(selectedCase.id)
      setAnalysis(null)
      setCopilotAnswer(null)
      await refreshSelected()
      setNotice('Evaluation saved. Review the persisted decision and evidence below.')
    })
  }

  const generateDraft = () => {
    if (!selectedCase) return
    void mutate('draft', async () => {
      const result = await api.draftMessage(selectedCase.id)
      setAnalysis(null)
      setCopilotAnswer(null)
      setDraft(result.draft_message || '')
      setSavedDraft(result.draft_message || '')
      await refreshSelected()
      setCopilot(result)
      setNotice('Draft generated and saved to the case timeline. It has not been sent.')
    })
  }

  const runCopilotAnalysis = () => {
    if (!selectedCase) return
    void mutate('copilot-analysis', async () => {
      const result = await api.analyzeCopilot(selectedCase.id)
      await refreshSelected()
      setCopilotAnswer(null)
      setCopilot(null)
      setAnalysis(result)
      setNotice(result.metadata.fallback_used
        ? 'Deterministic case analysis is shown; the AI explanation was unavailable. The persisted policy remains authoritative.'
        : 'AI case analysis generated. The deterministic policy remains authoritative.')
    })
  }

  const runRecoveryAgent = () => {
    if (!selectedCase) return
    void mutate('agent-run', async () => {
      setAgentRun(null)
      const result = await api.runAgent(
        selectedCase.id,
        'Review persisted synthetic recovery options and connection risks. Explain the policy decision using only returned tool evidence.',
      )
      await refreshSelected()
      setAgentRun(result)
      setNotice(result.metadata.fallback_used
        ? 'Deterministic recovery workflow completed; the AI agent was unavailable. No proposal or action was created.'
        : 'AI recovery agent completed in read-only mode. No proposal or action was created.')
    })
  }

  const askCopilot = () => {
    if (!selectedCase || !copilotQuestion.trim()) return
    void mutate('copilot-ask', async () => {
      const result = await api.askCopilot(selectedCase.id, copilotQuestion.trim())
      await refreshSelected()
      setAnalysis(null)
      setCopilot(null)
      setCopilotAnswer(result)
    })
  }

  const createAction = () => {
    if (!selectedCase?.latest_decision) return
    void mutate('propose', async () => {
      const key = globalThis.crypto?.randomUUID?.() || 'demo-' + selectedCase.id + '-' + Date.now()
      await api.proposeAction(selectedCase.id, {
        action_type: activeActionType,
        idempotency_key: key,
        decision_id: selectedCase.latest_decision!.decision_id,
        ...(activeActionType === 'REBOOK' ? { inventory_id: activeCandidateId } : {}),
        ...(activeActionType === 'SEND_MESSAGE' ? { message: draft } : {}),
      })
      await refreshSelected()
      setNotice('Action proposal created and is awaiting explicit approval.')
    })
  }

  const resolveAction = (kind: 'approve' | 'reject', action: RecoveryAction) => {
    setConfirm(null)
    void mutate(action.action_id, async () => {
      if (kind === 'approve') await api.approve(action.action_id)
      else await api.reject(action.action_id, 'Rejected by demo operator')
      await refreshSelected()
      setNotice(kind === 'approve'
        ? 'Simulator finished. The result is synthetic and did not change a real booking or send a real message.'
        : 'Proposal rejected. It cannot be executed.')
    })
  }

  const submitDemo = (flightId: string) => {
    const eventId = 'evt-demo-' + Date.now()
    void mutate('demo-alert', async () => {
      const result = await api.flightAlert({ event_id: eventId, fid: flightId.trim(), changes: { status: 'cancel' } })
      setDemoOpen(false)
      await refreshSelected()
      setNotice(result.duplicate
        ? 'Event ' + result.event_id + ' was already processed. No duplicate cases were created.'
        : 'Synthetic cancellation processed. ' + result.cases_created + ' new case' + (result.cases_created === 1 ? '' : 's') + ' created.')
      if (!result.duplicate) {
        const latestCases = await api.cases()
        const newest = [...latestCases].sort((a, b) => b.id - a.id).find((item) => item.flight_id === flightId.trim())
        if (newest) {
          setSelectedId(newest.id)
          setView('cases')
          await refreshSelected()
        }
      }
    })
  }

  const filteredCases = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return cases.filter((item) => {
      const code = details[item.id]?.latest_decision?.decision_code
      const matchesText = matchesCaseSearch(item, details[item.id], needle)
      const matchesFilter = filter === 'all' ||
        (filter === 'open' && item.status === 'open') ||
        (filter === 'evaluated' && Boolean(code)) ||
        (filter === 'attention' && ['escalate', 'manual_review'].includes(code || ''))
      return matchesText && matchesFilter
    })
  }, [cases, details, query, filter])

  const timeline = useMemo(() => Object.values(details).flatMap((detail) =>
    (detail.audit_timeline || []).map((event) => ({ ...event, caseId: detail.id, bookingRef: detail.booking_ref })),
  ).sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime()), [details])

  const openCount = cases.filter((item) => item.status === 'open').length
  const recommendationCount = cases.filter((item) => details[item.id]?.latest_decision?.decision_code === 'recommend_for_review').length
  const attentionCount = cases.filter((item) => ['escalate', 'manual_review'].includes(details[item.id]?.latest_decision?.decision_code || '')).length
  const pendingCount = Object.values(actions).filter((item) => item.state === 'PENDING_APPROVAL').length

  const decision = selectedCase?.latest_decision
  const canMessage = Boolean(savedDraft && draft === savedDraft)
  const allowedTypes: ActionType[] = []
  if (decision?.decision_code === 'recommend_for_review' && decision.candidates.length) allowedTypes.push('REBOOK')
  if (decision && ['manual_review', 'escalate'].includes(decision.decision_code)) allowedTypes.push('REFUND_REVIEW')
  if (decision && canMessage) allowedTypes.push('SEND_MESSAGE')
  const activeActionType = allowedTypes.includes(actionType) ? actionType : allowedTypes[0] || 'REBOOK'
  const activeCandidateId = decision?.candidates.some((item) => item.inventory_id === candidateId)
    ? candidateId
    : decision?.candidates[0]?.inventory_id || ''

  return (
    <div className="app-shell flex min-h-screen">
      <aside className="sidebar">
        <div className="brand"><div className="brand-mark"><Icon name="plane" size={21} /></div><div className="brand-copy"><strong>flyo</strong><span>DISRUPTION DESK</span></div></div>
        <div className="nav-label">WORKSPACE</div>
        <nav className="primary-nav" aria-label="Main navigation">
          <NavButton active={view === 'operations' && selectedId === null} icon="grid" label="Operations" onClick={() => { setView('operations'); setSelectedId(null) }} />
          <NavButton active={view === 'cases' && selectedId === null} icon="cases" label="Cases" count={cases.length} onClick={() => { setView('cases'); setSelectedId(null) }} />
          <NavButton active={view === 'activity' && selectedId === null} icon="activity" label="Activity Log" onClick={() => { setView('activity'); setSelectedId(null) }} />
        </nav>
        <div className="sidebar-bottom"><div className="sidebar-rule" /><div className="dataset-label"><span className="dataset-dot" /><div><strong>Synthetic environment</strong><small>Local demo data only</small></div></div><div className="operator"><div className="operator-avatar">DD</div><div><strong>Demo operator</strong><small>Review workspace</small></div><span className="operator-menu">•••</span></div></div>
      </aside>
      <main className="main-area">
        <header className="topbar">
          <div className="topbar-context"><span>FLYO AI</span><Icon name="chevron" size={14} /><strong>{selectedCase ? 'Case #' + selectedCase.id : view === 'operations' ? 'Operations' : view === 'cases' ? 'Cases' : 'Activity log'}</strong></div>
          <div className="topbar-right"><div className={'connection-state ' + (connected === false ? 'connection-offline' : '')}><span className="connection-dot" />{connected === null ? 'Checking API' : connected ? 'API connected' : 'API unavailable'}</div><span className="demo-badge"><i /> DEMO MODE</span><button className="button button-demo" type="button" onClick={() => setDemoOpen(true)} disabled={connected === false}><Icon name="plus" size={16} />Trigger demo event</button></div>
        </header>
        <div className="content-area">
          {error && <div className="alert-banner error-banner" role="alert"><Icon name="alert" /><span>{error}</span><button className="icon-button" aria-label="Dismiss error" onClick={() => setError('')}><Icon name="close" size={16} /></button></div>}
          {notice && <div className="alert-banner notice-banner" role="status"><Icon name="check" /><span>{notice}</span><button className="icon-button" aria-label="Dismiss notice" onClick={() => setNotice('')}><Icon name="close" size={16} /></button></div>}
          {selectedCase ? (
            <CaseWorkspace detail={selectedCase} actions={selectedActions} draft={draft} savedDraft={savedDraft} copilot={copilot} analysis={analysis} agentRun={agentRun} copilotAnswer={copilotAnswer} copilotQuestion={copilotQuestion} actionType={actionType} candidateId={candidateId} busy={busy}
              onBack={() => setSelectedId(null)} onRefresh={() => void refreshSelected()} onEvaluate={() => runEvaluation()} onDraft={() => generateDraft()} onDraftChange={setDraft} onActionType={setActionType} onCandidate={setCandidateId} onPropose={() => createAction()}
              onAnalyze={runCopilotAnalysis} onRunAgent={runRecoveryAgent} onAsk={askCopilot} onQuestionChange={setCopilotQuestion}
              onApprove={(action) => setConfirm({ kind: 'approve', action })} onReject={(action) => setConfirm({ kind: 'reject', action })} />
          ) : view === 'activity' ? <ActivityPage items={timeline} onOpenCase={openCase} loading={loading} /> : (
            <section>
              <div className="page-heading"><div><div className="eyebrow">{view === 'operations' ? 'CONTROL ROOM' : 'WORKSPACE'} <span className="eyebrow-line" /></div><h1>{view === 'operations' ? 'Operations overview' : 'Case queue'}</h1><p>{view === 'operations' ? 'Monitor disruption cases and coordinate recovery review.' : 'Search and review synthetic passenger disruption cases.'}</p></div><button className="button button-secondary" onClick={() => void refreshQuietly()} type="button" disabled={refreshing}><Icon name="refresh" size={16} />{refreshing ? 'Refreshing…' : 'Refresh data'}</button></div>
              {view === 'operations' && <div className="metric-grid"><Metric label="Total cases" value={cases.length} icon="cases" detail="Local demo dataset" tone="navy" /><Metric label="Open cases" value={openCount} icon="clock" detail="Awaiting review or resolution" tone="amber" /><Metric label="Recommendations ready" value={recommendationCount} icon="route" detail="Require human approval" tone="teal" /><Metric label="Needs attention" value={attentionCount} icon="alert" detail="Escalated or manual review" tone="rose" /></div>}
              <Queue cases={filteredCases} totalCases={cases.length} details={details} query={query} filter={filter} loading={loading} view={view} onQuery={setQuery} onFilter={setFilter} onOpen={openCase} onDemo={() => setDemoOpen(true)} />
              <div className="dashboard-foot"><span><Icon name="shield" size={14} />All records and outcomes are simulated.</span><span>{pendingCount} action{pendingCount === 1 ? '' : 's'} awaiting approval</span></div>
            </section>
          )}
        </div>
      </main>
      {demoOpen && <DemoDialog busy={busy === 'demo-alert'} onClose={() => setDemoOpen(false)} onSubmit={submitDemo} />}
      {confirm && <ConfirmDialog kind={confirm.kind} action={confirm.action} busy={busy === confirm.action.action_id} onCancel={() => setConfirm(null)} onConfirm={() => resolveAction(confirm.kind, confirm.action)} />}
    </div>
  )
}

function NavButton({ active, icon, label, count, onClick }: { active: boolean; icon: IconName; label: string; count?: number; onClick: () => void }) {
  return <button className={'nav-item ' + (active ? 'nav-item-active' : '')} onClick={onClick} type="button" aria-label={label}><Icon name={icon} size={17} /><span>{label}</span>{count !== undefined && count > 0 && <span className="nav-count">{count}</span>}</button>
}

function Metric({ label, value, icon, detail, tone }: { label: string; value: number; icon: IconName; detail: string; tone: string }) {
  return <article className="metric-card"><div className={'metric-icon metric-' + tone}><Icon name={icon} size={18} /></div><span className="metric-label">{label}</span><strong className="metric-value">{value}</strong><span className="metric-detail">{detail}</span><div className="metric-spark" aria-hidden="true"><i /><i /><i /><i /><i /><i /><i /></div></article>
}

function Queue({ cases, totalCases, details, query, filter, loading, view, onQuery, onFilter, onOpen, onDemo }: {
  cases: CaseListItem[]; totalCases: number; details: Record<number, CaseDetail>; query: string; filter: string; loading: boolean; view: View
  onQuery: (value: string) => void; onFilter: (value: string) => void; onOpen: (id: number) => void; onDemo: () => void
}) {
  return <section className="panel queue-panel">
    <div className="panel-heading queue-heading"><div><div className="panel-title-line"><h2>{view === 'operations' ? 'Disruption queue' : 'All cases'}</h2><span className="count-chip">{cases.length}</span></div><p>Cases from the local synthetic dataset</p></div><div className="queue-controls"><div className="search-box"><Icon name="search" size={16} /><label className="sr-only" htmlFor="case-search">Search cases</label><input id="case-search" value={query} onChange={(event) => onQuery(event.target.value)} placeholder="Search name, flight, PNR, case or airport…" />{query && <button className="search-clear" type="button" aria-label="Clear search" onClick={() => onQuery('')}><Icon name="close" size={14} /></button>}</div><label className="filter-box"><Icon name="filter" size={15} /><span className="sr-only">Filter cases</span><select value={filter} onChange={(event) => onFilter(event.target.value)}><option value="all">All cases</option><option value="open">Open</option><option value="evaluated">Evaluated</option><option value="attention">Needs attention</option></select></label></div></div>
    {loading ? <div className="loading-state"><span className="spinner" />Loading case data…</div> : cases.length === 0 && totalCases === 0 ? <div className="empty-state"><div className="empty-icon"><Icon name="plane" size={22} /></div><h3>No disruption cases yet</h3><p>Trigger a synthetic flight cancellation to create cases from seeded demo bookings.</p><button className="button button-primary" type="button" onClick={onDemo}><Icon name="plus" size={16} />Trigger demo event</button></div> : cases.length === 0 ? <div className="empty-state"><div className="empty-icon"><Icon name="search" size={22} /></div><h3>No matching cases</h3><p>Try a different passenger, flight, PNR, case ID, or airport code.</p>{query && <button className="button button-secondary" type="button" onClick={() => onQuery('')}>Clear search</button>}</div> : <>
      <div className="queue-scroll"><div className="queue-table queue-table-head"><span>CASE / BOOKING</span><span>PASSENGER</span><span>DISRUPTION</span><span>RISK</span><span>STATUS</span><span>RECOMMENDATION</span><span /></div>
        <div className="queue-rows">{cases.map((item) => {
          const detail = details[item.id]
          const evaluation = detail?.latest_decision
          const connection = evaluation?.connection_assessments.find((entry) => entry.status !== 'feasible')
          const eventStatus = detail?.audit_timeline.find((entry) => entry.action === 'flight_event_received')?.details.status
          const disruptionLabel = eventStatus === 'cancel' ? 'Cancellation' : eventStatus === 'delay' ? 'Significant delay' : eventStatus === 'missed_connection' ? 'Missed connection' : 'Disruption'
          const risk = ['escalate', 'manual_review'].includes(evaluation?.decision_code || '') ? 'Review' : connection ? 'Elevated' : evaluation ? 'Screened' : 'Unassessed'
          return <button className="queue-table queue-row" type="button" key={item.id} onClick={() => onOpen(item.id)}>
            <span className="case-primary"><strong>CS-{String(item.id).padStart(4, '0')}</strong><small>{item.booking_ref || 'Booking unavailable'}</small></span>
            <span className="passenger-cell"><strong>{item.passenger_name || 'Passenger unavailable'}</strong><small>Passenger</small></span>
            <span className="flight-cell"><i className="mini-flight"><Icon name="plane" size={13} /></i><span><strong>{item.flight_id || 'Flight unknown'}</strong><small>{detail?.itinerary.find((leg) => leg.flight_id === item.flight_id)?.airline_name || 'Synthetic flight'} · {disruptionLabel}</small></span></span>
            <span><RiskTag risk={risk} /></span><span><Badge tone={item.status === 'open' ? 'blue' : 'slate'}>{item.status || 'Unknown'}</Badge></span>
            <span><Badge tone={decisionTone(evaluation?.decision_code)}>{decisionText(evaluation?.decision_code)}</Badge></span><span className="row-chevron"><Icon name="chevron" size={17} /></span>
          </button>
        })}</div>
      </div><div className="queue-footer"><span>Showing {cases.length} of {totalCases} case{totalCases === 1 ? '' : 's'}</span><span><i className="dataset-dot" />Synthetic records only</span></div>
    </>}
  </section>
}

function RiskTag({ risk }: { risk: string }) {
  const tone = risk === 'Review' || risk === 'Elevated' ? 'risk-high' : risk === 'Screened' ? 'risk-low' : 'risk-neutral'
  return <span className={'risk-tag ' + tone}><i />{risk}</span>
}

function ProviderBadge({ metadata }: { metadata: CopilotMetadata | null }) {
  if (!metadata) return <span className="provider-badge provider-pending">Provider reported with response</span>
  const gemini = metadata.provider === 'gemini' && !metadata.fallback_used
  return <span className={'provider-badge ' + (gemini ? 'provider-gemini' : 'provider-fallback')}>
    <i />{gemini ? 'GEMINI · ' + (metadata.model || 'configured model') : 'DETERMINISTIC FALLBACK'}
    {metadata.validation_status !== 'validated' && <small>{pretty(metadata.validation_status)}</small>}
  </span>
}

function EvidenceRefs({ items }: { items: string[] }) {
  if (!items.length) return null
  return <div className="evidence-refs"><span>Evidence</span>{items.map((item) => <code key={item} title={item}>{item.replaceAll('.', ' · ')}</code>)}</div>
}

function AgentValue({ value }: { value: unknown }) {
  if (value === null) return <span className="agent-value-empty">Not provided</span>
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return <span>{String(value)}</span>
  }
  if (Array.isArray(value)) {
    return value.length
      ? <ul className="agent-value-list">{value.map((item, index) => <li key={index}><AgentValue value={item} /></li>)}</ul>
      : <span className="agent-value-empty">None</span>
  }
  if (typeof value === 'object') {
    const entries = Object.entries(value)
    return entries.length
      ? <dl className="agent-value-object">{entries.map(([key, item]) => <div key={key}><dt>{pretty(key)}</dt><dd><AgentValue value={item} /></dd></div>)}</dl>
      : <span className="agent-value-empty">No details</span>
  }
  return <span className="agent-value-empty">Unavailable</span>
}

function EvidenceList({ title, items }: { title: string; items: { text: string; evidence_refs: string[] }[] }) {
  if (!items.length) return null
  return <div className="copilot-result-block"><strong>{title}</strong><ul>{items.map((item, index) => <li key={title + index}><span>{item.text}</span><EvidenceRefs items={item.evidence_refs} /></li>)}</ul></div>
}

function StringList({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null
  return <div className="copilot-result-block"><strong>{title}</strong><ul>{items.map((item, index) => <li key={title + index}>{item}</li>)}</ul></div>
}

function CaseWorkspace({ detail, actions, draft, savedDraft, copilot, analysis, agentRun, copilotAnswer, copilotQuestion, actionType, candidateId, busy, onBack, onRefresh, onEvaluate, onDraft, onDraftChange, onActionType, onCandidate, onPropose, onAnalyze, onRunAgent, onAsk, onQuestionChange, onApprove, onReject }: {
  detail: CaseDetail; actions: RecoveryAction[]; draft: string; savedDraft: string; copilot: CaseCopilotResponse | null
  analysis: CopilotAnalysisResponse | null; agentRun: AgentRunResponse | null; copilotAnswer: CopilotAskResponse | null; copilotQuestion: string
  actionType: ActionType; candidateId: string; busy: string
  onBack: () => void; onRefresh: () => void; onEvaluate: () => void; onDraft: () => void; onDraftChange: (value: string) => void
  onActionType: (value: ActionType) => void; onCandidate: (value: string) => void; onPropose: () => void
  onAnalyze: () => void; onRunAgent: () => void; onAsk: () => void; onQuestionChange: (value: string) => void
  onApprove: (action: RecoveryAction) => void; onReject: (action: RecoveryAction) => void
}) {
  const decision = detail.latest_decision
  const receivedEvent = detail.audit_timeline.find((item) => item.action === 'flight_event_received')
  const warningConnections = decision?.connection_assessments.filter((item) => item.status !== 'feasible') || []
  const allowed: ActionType[] = []
  if (decision?.decision_code === 'recommend_for_review' && decision.candidates.length) allowed.push('REBOOK')
  if (decision && ['manual_review', 'escalate'].includes(decision.decision_code)) allowed.push('REFUND_REVIEW')
  if (decision && draft && draft === savedDraft) allowed.push('SEND_MESSAGE')
  const activeActionType = allowed.includes(actionType) ? actionType : allowed[0] || 'REBOOK'
  const activeCandidateId = decision?.candidates.some((item) => item.inventory_id === candidateId)
    ? candidateId
    : decision?.candidates[0]?.inventory_id || ''
  return <section className="case-workspace">
    <div className="case-back-row"><button className="text-button" onClick={onBack} type="button"><span className="back-arrow">←</span>Back to queue</button><button className="button button-secondary button-small" onClick={onRefresh} type="button"><Icon name="refresh" size={15} />Refresh case</button></div>
    <div className="detail-heading"><div><div className="eyebrow">CASE WORKSPACE <span className="eyebrow-line" /></div><div className="detail-title-row"><h1>Case #{String(detail.id).padStart(4, '0')}</h1><Badge tone="blue">{detail.status || 'Unknown status'}</Badge></div><p>{detail.booking_ref || 'Booking unavailable'} · {detail.passenger_name || 'Passenger unavailable'} · Opened {dateLabel(detail.created_at)}</p></div><button className="button button-primary" onClick={onEvaluate} type="button" disabled={busy === 'evaluate'}><Icon name="route" size={16} />{busy === 'evaluate' ? 'Evaluating…' : decision ? 'Re-evaluate case' : 'Evaluate case'}</button></div>
    <div className="detail-grid">
      <div className="detail-main-column">
        <section className="panel overview-panel"><div className="section-heading"><div><span className="section-kicker">DISRUPTION OVERVIEW</span><h2>Affected journey</h2></div><Badge tone="amber">{detail.disruption_reason || (receivedEvent?.details.status === 'cancel' ? 'Flight cancellation' : 'Flight disruption')}</Badge></div><div className="overview-data">
          <div className="overview-cell"><span>AFFECTED FLIGHT</span><strong><Icon name="plane" size={17} />{detail.flight_id || 'Unavailable'}</strong><small>{detail.itinerary.find((leg) => leg.flight_id === detail.flight_id)?.airline_name || 'Synthetic flight'} · Event {detail.event_id || 'not available'}</small></div>
          <div className="overview-cell"><span>BOOKING REFERENCE</span><strong>{detail.booking_ref || 'Unavailable'}</strong><small>{detail.passenger_name || 'Passenger not provided'}</small></div>
          <div className="overview-cell"><span>CASE CREATED</span><strong><Icon name="calendar" size={16} />{dateLabel(detail.created_at, { month: 'short', day: 'numeric', year: 'numeric' })}</strong><small>{dateLabel(detail.created_at, { hour: 'numeric', minute: '2-digit' })}</small></div>
        </div></section>

        <section className="panel itinerary-panel"><div className="section-heading"><div><span className="section-kicker">BOOKING DETAILS</span><h2>Itinerary & connection checks</h2></div><span className="subtle-count">{detail.itinerary?.length || 0} legs</span></div>
          {!detail.itinerary?.length ? <div className="inline-empty"><Icon name="alert" size={17} /><span>Itinerary data is not available for this booking. Missing details will be escalated for review.</span></div> : <div className="itinerary-list">{detail.itinerary.map((leg, index) => {
            const connection = decision?.connection_assessments.find((item) => item.first_flight_id === leg.flight_id && item.next_flight_id === detail.itinerary[index + 1]?.flight_id)
            return <div className="itinerary-block" key={leg.sequence + '-' + leg.flight_id}><div className="leg-row"><div className="leg-marker"><span>{String(leg.sequence).padStart(2, '0')}</span><i /></div><div className="leg-content"><div className="leg-head"><strong>{leg.flight_id || 'Flight unavailable'}</strong><span className="leg-airports">{leg.origin || '???'} <Icon name="arrow" size={13} /> {leg.destination || '???'}</span></div><div className="leg-times"><div><span>DEPARTS</span><strong>{dateInZone(leg.scheduled_departure, leg.departure_timezone)}</strong><small>{leg.departure_timezone || 'Timezone missing'}</small></div><div><span>ARRIVES</span><strong>{dateInZone(leg.scheduled_arrival, leg.arrival_timezone)}</strong><small>{leg.arrival_timezone || 'Timezone missing'}</small></div></div></div></div>
              {index < detail.itinerary.length - 1 && <div className={'connection-row ' + (connection && connection.status !== 'feasible' ? 'connection-risk' : '')}><div className="connection-line" />{connection ? <div className="connection-info"><Icon name={connection.status === 'feasible' ? 'check' : 'alert'} size={14} /><span>{connection.available_minutes == null ? 'Unavailable' : connection.available_minutes + ' min'} connection</span><strong>{pretty(connection.status)}</strong><small>Minimum {connection.minimum_minutes} min · timing screen only</small></div> : <div className="connection-info connection-undetermined"><Icon name="alert" size={14} /><span>Connection not evaluated</span><small>Run an evaluation to assess route and buffer data.</small></div>}</div>}
            </div>
          })}</div>}
          {warningConnections.length > 0 && <div className="warning-box"><Icon name="alert" size={16} /><div><strong>Connection needs human review</strong><span>{warningConnections.flatMap((item) => item.reason_codes).map(pretty).join(' · ')}</span></div></div>}
          <div className="fine-print"><Icon name="shield" size={13} />Timing screening is not official airport or airline connection validation.</div>
        </section>

        <section className="panel recommendation-panel"><div className="section-heading"><div><span className="section-kicker">DETERMINISTIC POLICY</span><h2>Recovery recommendation</h2></div>{decision && <Badge tone={decisionTone(decision.decision_code)}>{decisionText(decision.decision_code)}</Badge>}</div>
          {!decision ? <div className="inline-empty"><Icon name="route" size={17} /><span>No persisted evaluation yet. Run an evaluation to assess risks and synthetic alternatives.</span></div> : <>
            <div className="decision-summary"><span className={'decision-icon ' + (decision.decision_code === 'escalate' ? 'decision-warning' : 'decision-ok')}><Icon name={decision.decision_code === 'escalate' ? 'alert' : 'route'} size={18} /></span><div><strong>{decision.explanation || 'No explanation was returned.'}</strong><small>Policy {decision.policy_id || 'unknown'} · v{decision.policy_version || 'unknown'} · Decision #{decision.decision_id}</small></div></div>
            {decision.reason_codes?.length > 0 && <div className="reason-list" aria-label="Decision reason codes">{decision.reason_codes.map((reason) => <span className="reason-chip" key={reason}>{pretty(reason)}</span>)}</div>}
            {decision.candidates?.length ? <div className="alternatives-block"><div className="alternatives-title"><strong>Simulated alternatives</strong><span>Availability is not real inventory</span></div>{decision.candidates.map((candidate) => <Alternative candidate={candidate} selected={candidate.inventory_id === candidateId} onSelect={() => onCandidate(candidate.inventory_id)} key={candidate.inventory_id} />)}<div className="rank-evidence"><span>Ranking evidence</span><div>{decision.candidates[0].ranking_factors?.map((factor) => <small key={factor}><Icon name="check" size={12} />{factor}</small>)}</div></div></div> : <div className="inline-empty"><Icon name="alert" size={16} /><span>No eligible simulated alternative was returned. Review the reason codes and assess manually.</span></div>}
            {decision.requires_human_approval && <div className="approval-note"><Icon name="shield" size={15} />Any proposed recovery action requires explicit human approval.</div>}
          </>}
        </section>

        <section className="panel action-panel"><div className="section-heading"><div><span className="section-kicker">HUMAN-IN-THE-LOOP</span><h2>Proposed actions</h2></div><span className="subtle-count">{actions.length} actions</span></div>
          {actions.length > 0 && <div className="action-list">{actions.map((action) => <ActionCard key={action.action_id} action={action} busy={busy === action.action_id} onApprove={() => onApprove(action)} onReject={() => onReject(action)} />)}</div>}
          {allowed.length > 0 ? <div className="propose-form"><div className="propose-form-copy"><strong>Create a proposal</strong><span>Nothing executes until you approve it.</span></div><div className="propose-controls">
            <label className="field-label">Action type<select value={activeActionType} onChange={(event) => onActionType(event.target.value as ActionType)}>{allowed.map((item) => <option value={item} key={item}>{item.replace('_', ' ')}</option>)}</select></label>
            {activeActionType === 'REBOOK' && <label className="field-label">Simulated option<select value={activeCandidateId} onChange={(event) => onCandidate(event.target.value)}>{decision?.candidates.map((item) => <option value={item.inventory_id} key={item.inventory_id}>{item.inventory_id} · {item.origin}–{item.destination} · score {item.score}</option>)}</select></label>}
            {actionType === 'SEND_MESSAGE' && <div className="proposal-message-hint"><Icon name="check" size={14} />Uses the saved, unedited draft shown below.</div>}
            <button className="button button-primary" type="button" onClick={onPropose} disabled={busy === 'propose' || !decision}><Icon name="plus" size={15} />{busy === 'propose' ? 'Creating…' : 'Propose action'}</button>
          </div></div> : <div className="no-action-note"><Icon name="shield" size={15} />{decision ? 'No action is eligible under this saved decision.' : 'Evaluate the case before proposing an action.'}</div>}
        </section>

        <section className="panel copilot-panel"><div className="section-heading"><div className="copilot-heading"><span className="copilot-mark"><Icon name="spark" size={16} /></span><div><span className="section-kicker">CASE COPILOT</span><h2>AI-assisted case review</h2></div></div><ProviderBadge metadata={analysis?.metadata || copilotAnswer?.metadata || copilot?.metadata || null} /></div><p className="copilot-description">Advisory summaries use this case’s persisted evidence. Deterministic policy controls eligibility. Generated content never proposes or executes actions.</p>
          <div className="copilot-tool-row"><div><strong>Bounded recovery agent</strong><small>Uses read-only tools to inspect persisted synthetic options and connection checks. No proposal permission is granted.</small></div><button className="button button-secondary" type="button" onClick={onRunAgent} disabled={busy === 'agent-run'}><Icon name="route" size={15} />{busy === 'agent-run' ? 'Running agent…' : 'Run AI Recovery Agent'}</button></div>
          {busy === 'agent-run' && <div className="agent-loading" role="status"><span className="spinner" />Running bounded analysis against persisted case evidence…</div>}
          {agentRun && <div className="copilot-result agent-result" aria-live="polite">
            <div className="copilot-result-heading"><strong>Recovery agent result</strong><div className="provider-result-meta"><ProviderBadge metadata={agentRun.metadata} /><Badge tone="slate">{agentRun.simulated ? 'Simulated' : 'Unverified'}</Badge></div></div>
            <p>{agentRun.summary}</p>
            <div className="agent-safety-note"><Icon name="shield" size={14} />{agentRun.approval_required ? 'Human approval remains required for any proposed action.' : 'No approval was requested.'} This run cannot approve, reject, or execute actions.</div>
            {agentRun.proposed_action_id && <div className="agent-proposal-note" role="status">Proposal {agentRun.proposed_action_id} is separate from this run and requires human approval in Proposed actions.</div>}
            <div className="copilot-result-block"><strong>Tool results</strong>
              {agentRun.tool_results.length === 0 ? <p className="agent-empty">The agent returned no tool results for this run.</p> : <div className="agent-tool-results">{agentRun.tool_results.map((item, index) => <section className="agent-tool-result" key={item.name + '-' + index}>
                <div className="agent-tool-heading"><strong>{pretty(item.name)}</strong><Badge tone={item.status === 'completed' ? 'green' : item.status === 'blocked' ? 'amber' : 'red'}>{pretty(item.status)}</Badge></div>
                <AgentValue value={item.result} />
              </section>)}</div>}
            </div>
          </div>}
          <div className="copilot-tool-row"><div><strong>Structured recovery analysis</strong><small>Summarize confirmed facts, risks, evidence and next steps.</small></div><button className="button button-secondary" type="button" onClick={onAnalyze} disabled={busy === 'copilot-analysis'}><Icon name="spark" size={15} />{busy === 'copilot-analysis' ? 'Analyzing…' : analysis ? 'Refresh analysis' : 'Analyze case'}</button></div>
          {analysis && <div className="copilot-result" aria-live="polite"><div className="copilot-result-heading"><strong>{analysis.analysis_status === 'complete' ? 'Analysis' : 'Limited analysis'}</strong><div className="provider-result-meta"><Badge tone={analysis.analysis_status === 'complete' ? 'blue' : 'amber'}>{pretty(analysis.analysis_status)}</Badge><ProviderBadge metadata={analysis.metadata} /></div></div><p>{analysis.disruption_summary}</p>
            <EvidenceList title="Confirmed facts" items={analysis.confirmed_facts} /><EvidenceList title="Risk findings" items={analysis.risk_findings} />
            <div className="copilot-result-block"><strong>Policy recommendation</strong><p>{analysis.recommendation_explanation}</p></div>
            <EvidenceList title="Trade-offs" items={analysis.trade_offs} />
            {analysis.missing_information.length > 0 && <StringList title="Missing information" items={analysis.missing_information} />}
            <div className="copilot-result-block"><strong>Suggested next step</strong><p>{analysis.recommended_next_step}</p></div><EvidenceRefs items={analysis.evidence_refs} />
          </div>}
          <div className="copilot-tool-row"><div><strong>Ask about this case</strong><small>Answers are limited to the case, evaluation, action and audit evidence.</small></div></div>
          <form className="copilot-ask-form" onSubmit={(event) => { event.preventDefault(); onAsk() }}><label className="field-label" htmlFor="copilot-question">Your question</label><textarea id="copilot-question" value={copilotQuestion} onChange={(event) => onQuestionChange(event.target.value)} rows={2} maxLength={1000} placeholder="Why was this case escalated?" /><button className="button button-secondary" type="submit" disabled={busy === 'copilot-ask' || !copilotQuestion.trim()}>{busy === 'copilot-ask' ? 'Answering…' : 'Ask about this case'}</button></form>
          {copilotAnswer && <div className="copilot-answer" aria-live="polite"><div className="copilot-result-heading"><strong>Case answer</strong><ProviderBadge metadata={copilotAnswer.metadata} /></div><p>{copilotAnswer.answer}</p><EvidenceRefs items={copilotAnswer.evidence_refs} />{copilotAnswer.missing_information.length > 0 && <StringList title="Information not available" items={copilotAnswer.missing_information} />}</div>}
          <div className="copilot-draft-divider"><span>Passenger communication</span></div><p className="copilot-description">Drafts are grounded in verified case details, are not sent, and do not create an action.</p>
          {copilot && <div className="copilot-summary"><strong>Case summary</strong><span>{copilot.case_summary}</span><strong>Deterministic recommendation</strong><span>{pretty(copilot.recommendation.decision_code)} · {copilot.recommendation.reason_codes.map(pretty).join(', ') || 'No reason codes'}</span>{copilot.warnings.length > 0 && <div className="copilot-warning"><Icon name="alert" size={14} />Review warnings: {copilot.warnings.map(pretty).join(', ')}</div>}<EvidenceRefs items={copilot.evidence_refs || []} /></div>}
          {draft ? <label className="draft-wrap"><span className="draft-label"><span><Icon name="send" size={13} />DRAFT — NOT SENT</span><small>{draft === savedDraft ? 'Saved draft' : 'Local edit · not saved to API'}</small></span><textarea aria-label="Editable passenger message draft" value={draft} onChange={(event) => onDraftChange(event.target.value)} rows={4} /></label> : <div className="draft-placeholder">Generate a case-grounded message for human review. This does not send or create an action.</div>}
          <div className="copilot-footer"><span>Generating or editing a draft has no downstream effect.</span><button className="button button-secondary" type="button" onClick={onDraft} disabled={busy === 'draft' || !decision}><Icon name="spark" size={15} />{busy === 'draft' ? 'Generating…' : draft ? 'Generate new draft' : 'Generate draft'}</button></div>
          {draft && draft !== savedDraft && <div className="edit-warning" role="status">This edit is local only. Generate a new saved draft before proposing a SEND_MESSAGE action.</div>}
        </section>
      </div>
      <aside className="detail-side-column">
        <section className="panel side-panel"><div className="side-panel-heading"><h3>Case record</h3><span className="record-id">#{detail.id}</span></div><div className="side-data-list"><Data label="Passenger" value={detail.passenger_name || 'Not provided'} /><Data label="Booking reference" value={detail.booking_ref || 'Not provided'} /><Data label="Affected flight" value={detail.flight_id || 'Not provided'} /><Data label="Disruption event" value={detail.event_id || 'Not available'} mono /><Data label="Case status" value={detail.status || 'Unknown'} /></div></section>
        <section className="panel side-panel"><div className="side-panel-heading"><h3>Evaluation status</h3><Icon name="route" size={17} /></div>{decision ? <div className="evaluation-card"><Badge tone={decisionTone(decision.decision_code)}>{decisionText(decision.decision_code)}</Badge><p>Saved {dateLabel(decision.evaluated_at)}</p><div className="evaluation-meta"><span>Policy</span><strong>{decision.policy_id} v{decision.policy_version}</strong><span>Human approval</span><strong>{decision.requires_human_approval ? 'Required' : 'Not required'}</strong><span>Side effects</span><strong>{decision.side_effects_executed ? 'Reported' : 'None executed'}</strong></div></div> : <div className="side-empty">No evaluation saved for this case.</div>}</section>
        <section className="panel side-panel timeline-panel"><div className="side-panel-heading"><h3>Case timeline</h3><span className="subtle-count">{detail.audit_timeline.length}</span></div>{detail.audit_timeline.length ? <ol className="timeline-list">{detail.audit_timeline.slice().reverse().map((item, index) => <li key={item.at + item.action + index}><span className={'timeline-dot ' + (item.action.includes('failed') || item.action.includes('rejected') ? 'timeline-dot-warning' : '')} /><div><strong>{timelineTitle(item.action)}</strong><small>{dateLabel(item.at)}</small><AuditDetail event={item} actions={actions} /></div></li>)}</ol> : <div className="side-empty">Timeline events appear here as this case is reviewed.</div>}</section>
        <div className="simulation-callout"><Icon name="shield" size={16} /><div><strong>Simulation only</strong><span>No airline, payment, or passenger service is contacted.</span></div></div>
      </aside>
    </div>
  </section>
}

function Alternative({ candidate, selected, onSelect }: { candidate: CandidateAlternative; selected: boolean; onSelect: () => void }) {
  return <button className={'alternative-card ' + (selected ? 'alternative-selected' : '')} type="button" onClick={onSelect} aria-pressed={selected}>
    <span className="alternative-radio">{selected && <i />}</span><span className="alternative-main"><strong>{candidate.origin} <Icon name="arrow" size={13} /> {candidate.destination}</strong><small>{candidate.flight_id} · {candidate.inventory_id}</small></span>
    <span className="alternative-time"><strong>{utcLabel(candidate.scheduled_departure)}</strong><small>Departure · UTC</small></span><span className="alternative-time"><strong>{utcLabel(candidate.scheduled_arrival)}</strong><small>Arrival · UTC</small></span><span className="score-pill">{candidate.score}<small>score</small></span><span className="simulated-tag">SIMULATED</span>
  </button>
}

function ActionCard({ action, busy, onApprove, onReject }: { action: RecoveryAction; busy: boolean; onApprove: () => void; onReject: () => void }) {
  const result = action.execution_result
  const alt = action.proposed_payload.alternative as CandidateAlternative | undefined
  return <article className="action-card"><div className="action-card-top"><div><span className="action-type-icon"><Icon name={action.action_type === 'REBOOK' ? 'plane' : action.action_type === 'SEND_MESSAGE' ? 'send' : 'cases'} size={15} /></span><strong>{pretty(action.action_type)}</strong><small>{action.action_id.slice(0, 8)}…</small></div><Badge tone={actionTone(action.state)}>{pretty(action.state)}</Badge></div>
    <p>{action.action_type === 'REBOOK' ? 'Proposed option ' + (alt?.inventory_id || 'not available') + ' · synthetic inventory' : action.action_type === 'REFUND_REVIEW' ? 'Simulated review request · no money movement' : 'Saved passenger draft · not sent'}</p>
    {result && <div className={'execution-result ' + (action.state === 'FAILED' ? 'execution-failed' : '')}><Icon name={action.state === 'FAILED' ? 'alert' : 'check'} size={15} /><span><strong>{String(result.outcome || result.failure_reason || action.failure_reason || 'Execution result')}</strong><small>Simulation result · real-world completion: no</small></span></div>}
    {action.state === 'PENDING_APPROVAL' ? <div className="action-buttons"><button className="button button-secondary button-small" type="button" onClick={onReject} disabled={busy}>Reject</button><button className="button button-primary button-small" type="button" onClick={onApprove} disabled={busy}><Icon name="check" size={14} />Approve</button></div> : <div className="action-completed-note">State recorded {dateLabel(action.executed_at || action.approved_at || action.created_at)}</div>}
  </article>
}

function Data({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return <div className="data-item"><span>{label}</span><strong className={mono ? 'mono' : ''}>{value}</strong></div>
}

function AuditDetail({ event, actions }: { event: AuditTimelineItem; actions: RecoveryAction[] }) {
  const id = typeof event.details.action_id === 'string' ? event.details.action_id : ''
  const action = id ? actions.find((item) => item.action_id === id) : undefined
  if (action) return <small className="timeline-detail">{pretty(action.action_type)} · {pretty(action.state)}</small>
  if (typeof event.details.decision_code === 'string') return <small className="timeline-detail">{pretty(event.details.decision_code)}</small>
  if (typeof event.details.draft_only === 'boolean') return <small className="timeline-detail">Draft only · not sent</small>
  return null
}

function ActivityPage({ items, onOpenCase, loading }: { items: (AuditTimelineItem & { caseId: number; bookingRef: string })[]; onOpenCase: (id: number) => void; loading: boolean }) {
  return <section><div className="page-heading"><div><div className="eyebrow">AUDIT TRAIL <span className="eyebrow-line" /></div><h1>Activity log</h1><p>Chronological events recorded against synthetic disruption cases.</p></div></div><section className="panel activity-panel"><div className="panel-heading"><div><h2>Recent case activity</h2><p>Read-only history from the backend case timeline</p></div><span className="count-chip">{items.length}</span></div>
    {loading ? <div className="loading-state"><span className="spinner" />Loading activity…</div> : items.length === 0 ? <div className="empty-state"><div className="empty-icon"><Icon name="activity" size={22} /></div><h3>No activity recorded</h3><p>Once a synthetic disruption event is processed, its case history appears here.</p></div> : <ol className="activity-list">{items.map((item, index) => <li key={item.caseId + item.at + index}><div className="activity-time">{dateLabel(item.at, { month: 'short', day: 'numeric' })}<small>{dateLabel(item.at, { hour: 'numeric', minute: '2-digit' })}</small></div><span className={'activity-marker ' + (item.action.includes('failed') || item.action.includes('rejected') ? 'activity-marker-warning' : '')}><Icon name={item.action.includes('failed') || item.action.includes('rejected') ? 'alert' : 'check'} size={14} /></span><div className="activity-copy"><strong>{timelineTitle(item.action)}</strong><span>Case #{String(item.caseId).padStart(4, '0')} · {item.bookingRef}</span><small>{activityDetail(item)}</small></div><button className="text-button" type="button" onClick={() => onOpenCase(item.caseId)}>Open case <Icon name="arrow" size={13} /></button></li>)}</ol>}
  </section></section>
}

function timelineTitle(action: string) {
  const labels: Record<string, string> = {
    flight_event_received: 'Flight alert received', flight_event_processed: 'Flight disruption processed',
    case_created: 'Disruption case created', case_evaluated: 'Recovery evaluation saved',
    action_proposed: 'Action proposed', action_approved: 'Action approved by operator',
    action_rejected: 'Action proposal rejected', action_execution_started: 'Simulator execution started',
    action_succeeded: 'Simulated action succeeded', action_failed: 'Simulated action failed',
    action_transition_rejected: 'Invalid action transition blocked', message_draft_generated: 'Passenger message draft generated',
  }
  return labels[action] || pretty(action)
}

function activityDetail(item: AuditTimelineItem) {
  if (typeof item.details.decision_code === 'string') return 'Decision: ' + item.details.decision_code
  if (typeof item.details.action_type === 'string') return 'Action type: ' + item.details.action_type
  if (typeof item.details.fid === 'string') return 'Flight ' + item.details.fid
  if (typeof item.details.draft_only === 'boolean') return 'Draft only — not sent'
  return 'Recorded in the case audit trail'
}

function ConfirmDialog({ kind, action, busy, onCancel, onConfirm }: { kind: 'approve' | 'reject'; action: RecoveryAction; busy: boolean; onCancel: () => void; onConfirm: () => void }) {
  const approve = kind === 'approve'
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') onCancel() }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [onCancel])
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onCancel()}><section className="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="confirm-title">
    <button className="icon-button modal-close" aria-label="Close confirmation" onClick={onCancel} type="button"><Icon name="close" /></button><div className={'modal-icon ' + (approve ? 'modal-icon-green' : 'modal-icon-red')}><Icon name={approve ? 'shield' : 'close'} size={21} /></div>
    <h2 id="confirm-title">{approve ? 'Approve simulated action?' : 'Reject this proposal?'}</h2><p>{approve ? 'This runs the local simulator for ' + pretty(action.action_type).toLowerCase() + '. No real booking, refund, or message will be performed.' : 'This proposal will be marked rejected and cannot be executed later.'}</p>
    <div className="modal-action-summary"><span>Action</span><strong>{pretty(action.action_type)}</strong><span>Current state</span><strong>{pretty(action.state)}</strong></div>
    <div className="modal-buttons"><button className="button button-secondary" type="button" onClick={onCancel} disabled={busy} autoFocus>Cancel</button><button className={'button ' + (approve ? 'button-primary' : 'button-danger')} type="button" onClick={onConfirm} disabled={busy}>{busy ? 'Saving…' : approve ? 'Approve & simulate' : 'Reject proposal'}</button></div>
  </section></div>
}

function DemoDialog({ busy, onClose, onSubmit }: { busy: boolean; onClose: () => void; onSubmit: (flightId: string) => void }) {
  const [flightId, setFlightId] = useState('DEMO-AI-2101')
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [onClose])
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}><section className="confirm-modal demo-modal" role="dialog" aria-modal="true" aria-labelledby="demo-title">
    <button className="icon-button modal-close" aria-label="Close demo event" onClick={onClose} type="button"><Icon name="close" /></button><div className="modal-icon modal-icon-teal"><Icon name="plane" size={21} /></div><span className="section-kicker">SYNTHETIC WEBHOOK</span><h2 id="demo-title">Trigger a flight alert</h2>
    <p>This sends a supported cancellation event to the local demo API. Matching synthetic bookings receive new cases. Repeating this intentionally creates another event and case set.</p>
    <label className="field-label demo-field">Flight identifier<input autoFocus value={flightId} onChange={(event) => setFlightId(event.target.value)} placeholder="DEMO-AI-2101" /></label>
    <div className="event-preview"><span>EVENT PAYLOAD</span><code>{'{"event_id":"evt-demo-[unique]","fid":"' + (flightId || '…') + '","changes":{"status":"cancel"}}'}</code></div>
    <div className="modal-buttons"><button className="button button-secondary" type="button" onClick={onClose} disabled={busy}>Cancel</button><button className="button button-primary" type="button" onClick={() => onSubmit(flightId)} disabled={busy || !flightId.trim()}><Icon name="send" size={15} />{busy ? 'Sending event…' : 'Send synthetic event'}</button></div>
  </section></div>
}

export default App
