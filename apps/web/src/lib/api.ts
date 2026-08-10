import type { AnalysisResult, PartialAnalysisResult } from '../generated/analysis-result'
import type { ServerStatus } from './uiState'

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
    return JSON.stringify(body)
  } catch {
    return res.statusText || `HTTP ${res.status}`
  }
}

export async function mintDevToken(
  body: DevTokenRequest,
): Promise<{ token: string }> {
  const res = await fetch('/dev/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(await readError(res))
  return (await res.json()) as { token: string }
}

export async function createAnalysis(
  token: string,
  callAttemptId: string,
): Promise<AnalysisCreated> {
  const res = await fetch('/api/analyses', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ call_attempt_id: callAttemptId }),
  })
  if (!res.ok) throw new Error(await readError(res))
  return (await res.json()) as AnalysisCreated
}

export async function getAnalysis(
  token: string,
  analysisId: string,
): Promise<Analysis> {
  const res = await fetch(`/api/analyses/${analysisId}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!res.ok) throw new Error(await readError(res))
  return (await res.json()) as Analysis
}

export async function cancelAnalysis(
  token: string,
  analysisId: string,
): Promise<{ id: string; status: 'cancelled' }> {
  const res = await fetch(`/api/analyses/${analysisId}/cancel`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!res.ok) throw new Error(await readError(res))
  return (await res.json()) as { id: string; status: 'cancelled' }
}
