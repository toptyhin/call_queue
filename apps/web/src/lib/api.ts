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

async function readError(res: Response): Promise<string> {
  try {
    const body: unknown = await res.json()
    if (
      body &&
      typeof body === 'object' &&
      'message' in body &&
      typeof (body as { message: unknown }).message === 'string'
    ) {
      return (body as { message: string }).message
    }
    if (
      body &&
      typeof body === 'object' &&
      'detail' in body &&
      typeof (body as { detail: unknown }).detail === 'string'
    ) {
      return (body as { detail: string }).detail
    }
    if (
      body &&
      typeof body === 'object' &&
      'code' in body &&
      'detail' in body
    ) {
      const detail = (body as { detail: unknown }).detail
      if (typeof detail === 'string') return detail
    }
    return JSON.stringify(body)
  } catch {
    return res.statusText || `HTTP ${res.status}`
  }
}

export async function mintDevToken(
  body: DevTokenRequest,
): Promise<{ token: string }> {
  const res = await apiFetch('/dev/token', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(await readError(res))
  return (await res.json()) as { token: string }
}

export async function logoutDevToken(): Promise<void> {
  const res = await apiFetch('/dev/logout', { method: 'POST' })
  if (!res.ok) throw new Error(await readError(res))
}

export async function getDevSession(): Promise<DevSession> {
  const res = await apiFetch('/dev/session')
  if (!res.ok) throw new Error(await readError(res))
  return (await res.json()) as DevSession
}

export async function listCallAttempts(opts?: {
  limit?: number
  cursor?: string | null
}): Promise<CallAttemptListResponse> {
  const params = new URLSearchParams()
  if (opts?.limit) params.set('limit', String(opts.limit))
  if (opts?.cursor) params.set('cursor', opts.cursor)
  const qs = params.toString()
  const res = await apiFetch(`/api/call_attempts${qs ? `?${qs}` : ''}`)
  if (!res.ok) throw new Error(await readError(res))
  return (await res.json()) as CallAttemptListResponse
}

export async function getCallAttempt(
  attemptId: string,
): Promise<CallAttemptDetail> {
  const res = await apiFetch(`/api/call_attempts/${attemptId}`)
  if (!res.ok) throw new Error(await readError(res))
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
  if (!res.ok) throw new Error(await readError(res))
  return (await res.json()) as AnalysisCreated
}

export async function getAnalysis(analysisId: string): Promise<Analysis> {
  const res = await apiFetch(`/api/analyses/${analysisId}`)
  if (!res.ok) throw new Error(await readError(res))
  return (await res.json()) as Analysis
}

export async function cancelAnalysis(
  analysisId: string,
): Promise<{ id: string; status: 'cancelled' }> {
  const res = await apiFetch(`/api/analyses/${analysisId}/cancel`, {
    method: 'POST',
  })
  if (!res.ok) throw new Error(await readError(res))
  return (await res.json()) as { id: string; status: 'cancelled' }
}
