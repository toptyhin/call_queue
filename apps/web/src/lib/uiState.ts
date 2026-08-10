import type { PartialAnalysisResult } from '../generated/analysis-result'
import { isPartialEmpty } from './partial'

/** Server-side analysis status (OpenAPI AnalysisStatus). */
export type ServerStatus =
  | 'queued'
  | 'streaming'
  | 'done'
  | 'error'
  | 'cancelled'

export type ConnectionStatus = 'idle' | 'open' | 'reconnecting' | 'closed'

/**
 * Client UI state for `data-state` (TZ 7.4).
 * Priority is evaluated top → bottom.
 */
export type UiState =
  | 'idle'
  | 'cancelled'
  | 'error'
  | 'partial'
  | 'done'
  | 'reconnecting'
  | 'streaming'
  | 'queued'

export function deriveUiState(input: {
  serverStatus: ServerStatus | null
  connectionStatus: ConnectionStatus
  chunksReceived: number
  partial: PartialAnalysisResult | null
}): UiState {
  const { serverStatus, connectionStatus, chunksReceived, partial } = input

  if (serverStatus === null) return 'idle'

  // 1. cancelled
  if (serverStatus === 'cancelled') return 'cancelled'

  // 2–3. error / partial (error with non-empty partial)
  if (serverStatus === 'error') {
    return isPartialEmpty(partial) ? 'error' : 'partial'
  }

  // 4. done
  if (serverStatus === 'done') return 'done'

  // 5. reconnecting (transport down)
  if (connectionStatus === 'reconnecting') return 'reconnecting'

  // 6. streaming — ≥1 chunk, connection open
  if (chunksReceived >= 1 && connectionStatus === 'open') return 'streaming'

  // 7. queued — analysis created, no chunks yet
  return 'queued'
}
