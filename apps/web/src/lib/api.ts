import type { AnalysisResult, PartialAnalysisResult } from '../generated/analysis-result'
import type { ServerStatus } from './uiState'

const JSON_HEADERS = { 'Content-Type': 'application/json' }

/** Same-origin fetch that sends the HttpOnly `dev_token` cookie. */
function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
  return fetch(input, {
    ...init,
    credentials: 'include',
    headers: init.headers,
  })
}

export type AnalysisCreated = {
  id: string
  status: 'queued'
}

export type Analysis = {
  id: string
  call_attempt_id: string
  status: ServerStatus
  result: AnalysisResult | null
  partial: PartialAnalysisResult | null
  error: string | null
  created_at: string
}

export type DevTokenRequest = {
  sub: string
  org_id: string
  role: 'authenticated' | 'worker'
  expires_in?: number
}

export type DevSession = {
  authenticated: boolean
  sub: string | null
  org_id: string | null
  role: 'authenticated' | 'worker' | null
}

export type CallAttemptStatus =
  | 'queued'
  | 'dialing'
  | 'in_progress'
  | 'completed'
  | 'failed'
  | 'no_answer'

export type CallAttemptListItem = {
  id: string
  status: CallAttemptStatus
  phone: string
  campaign_name: string
  started_at: string | null
  ended_at: string | null
  created_at: string
}

export type CallAttemptListResponse = {
  items: CallAttemptListItem[]
  next_cursor: string | null
}

export type StatusHistoryItem = {
  at: string
  status: CallAttemptStatus
  source: 'claim' | 'webhook' | 'abort'
  event_type: string | null
  sequence: number | null
}

export type CrmDelivery = {
  state: 'pending' | 'retrying' | 'delivered'
  attempts: number
  delivered_at: string | null
  last_error: string | null
  next_attempt_at: string | null
}

export type AnalysisSummary = {
  id: string
  status: ServerStatus
  result: AnalysisResult | null
  partial: PartialAnalysisResult | null
  error: string | null
  created_at: string
}

export type CallAttemptDetail = {
  id: string
  status: CallAttemptStatus
  provider_call_id: string | null
  campaign_name: string
  contact: { phone: string; timezone: string }
  started_at: string | null
  ended_at: string | null
  outcome: Record<string, unknown> | null
  transcript: string | null
  status_history: StatusHistoryItem[]
  analyses: AnalysisSummary[]
  crm: CrmDelivery | null
  created_at: string
}

export type CallFeedCrmEvent = {
  attempt_id: string
  crm: CrmDelivery | null
}

export type CallFeedAnalysisEvent = {
  attempt_id: string
  analysis_id: string
  status: ServerStatus
}

/** Machine-readable API error (`{code, detail}`) with a human message. */
export class ApiError extends Error {
  readonly code: string | null
  readonly status: number

  constructor(
    message: string,
    opts: { code?: string | null; status: number },
  ) {
    super(message)
    this.name = 'ApiError'
    this.code = opts.code ?? null
    this.status = opts.status
  }
}

async function readApiError(res: Response): Promise<ApiError> {
  let code: string | null = null
  let detail = res.statusText || `HTTP ${res.status}`
  try {
    const body: unknown = await res.json()
    if (body && typeof body === 'object') {
      const rec = body as Record<string, unknown>
      if (typeof rec.code === 'string') code = rec.code
      if (typeof rec.message === 'string') detail = rec.message
      else if (typeof rec.detail === 'string') detail = rec.detail
      else if (code === 'validation_error') detail = 'Некорректный запрос'
      else detail = `HTTP ${res.status}`
    }
  } catch {
    /* keep statusText */
  }
  return new ApiError(detail, { code, status: res.status })
}

export async function mintDevToken(
  body: DevTokenRequest,
): Promise<{ token: string }> {
  const res = await apiFetch('/dev/token', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
  })
  if (!res.ok) throw await readApiError(res)
  return (await res.json()) as { token: string }
}

export async function logoutDevToken(): Promise<void> {
  const res = await apiFetch('/dev/logout', { method: 'POST' })
  if (!res.ok) throw await readApiError(res)
}

export async function getDevSession(): Promise<DevSession> {
  const res = await apiFetch('/dev/session')
  if (!res.ok) throw await readApiError(res)
  return (await res.json()) as DevSession
}

export type ListCallAttemptsOpts = {
  limit?: number
  cursor?: string | null
  status?: CallAttemptStatus
  phone?: string
  createdFrom?: string
  createdTo?: string
}

export async function listCallAttempts(
  opts?: ListCallAttemptsOpts,
): Promise<CallAttemptListResponse> {
  const params = new URLSearchParams()
  if (opts?.limit) params.set('limit', String(opts.limit))
  if (opts?.cursor) params.set('cursor', opts.cursor)
  if (opts?.status) params.set('status', opts.status)
  if (opts?.phone) params.set('phone', opts.phone)
  if (opts?.createdFrom) params.set('created_from', opts.createdFrom)
  if (opts?.createdTo) params.set('created_to', opts.createdTo)
  const qs = params.toString()
  const res = await apiFetch(`/api/call_attempts${qs ? `?${qs}` : ''}`)
  if (!res.ok) throw await readApiError(res)
  return (await res.json()) as CallAttemptListResponse
}

export async function getCallAttempt(
  attemptId: string,
): Promise<CallAttemptDetail> {
  const res = await apiFetch(`/api/call_attempts/${attemptId}`)
  if (!res.ok) throw await readApiError(res)
  return (await res.json()) as CallAttemptDetail
}

export async function createAnalysis(
  callAttemptId: string,
): Promise<AnalysisCreated> {
  const res = await apiFetch('/api/analyses', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ call_attempt_id: callAttemptId }),
  })
  if (!res.ok) throw await readApiError(res)
  return (await res.json()) as AnalysisCreated
}

export async function getAnalysis(analysisId: string): Promise<Analysis> {
  const res = await apiFetch(`/api/analyses/${analysisId}`)
  if (!res.ok) throw await readApiError(res)
  return (await res.json()) as Analysis
}

export async function cancelAnalysis(
  analysisId: string,
): Promise<{ id: string; status: 'cancelled' }> {
  const res = await apiFetch(`/api/analyses/${analysisId}/cancel`, {
    method: 'POST',
  })
  if (!res.ok) throw await readApiError(res)
  return (await res.json()) as { id: string; status: 'cancelled' }
}
