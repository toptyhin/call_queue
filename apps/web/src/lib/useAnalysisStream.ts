import { useEffect, useMemo, useRef, useState } from 'react'
import { fetchEventSource } from '@microsoft/fetch-event-source'
import type { AnalysisResult, PartialAnalysisResult } from '../generated/analysis-result'
import {
  ApiError,
  cancelAnalysis,
  createAnalysis,
  getAnalysis,
} from './api'
import { formatAnalysisError } from './formatError'
import {
  buffersFromPartial,
  emptyBuffers,
  isAnalysisField,
  parseBuffers,
} from './partial'
import {
  deriveUiState,
  type ConnectionStatus,
  type ServerStatus,
  type UiState,
} from './uiState'

const ANALYSIS_ACTION_ERROR_RU: Record<string, string> = {
  attempt_not_completed: 'Разбор доступен только для завершённых звонков',
  no_transcript: 'Звонок завершился без транскрипта — разбор невозможен',
  analysis_terminal: 'Разбор уже завершён или отменён',
  not_found: 'Попытка не найдена',
}

function analysisActionMessage(err: unknown): string {
  if (err instanceof ApiError && err.code && ANALYSIS_ACTION_ERROR_RU[err.code]) {
    return ANALYSIS_ACTION_ERROR_RU[err.code]
  }
  return err instanceof Error ? err.message : String(err)
}

const MAX_RECONNECT_FAILURES = 5
const DEFAULT_RETRY_MS = 3000

export type AnalysisStream = {
  uiState: UiState
  analysisId: string | null
  serverStatus: ServerStatus | null
  connectionStatus: ConnectionStatus
  lastEventId: string | null
  display: AnalysisResult | PartialAnalysisResult | null
  errorMessage: string | null
  actionError: string | null
  busy: boolean
  start: (callAttemptId: string) => Promise<void>
  cancel: () => Promise<void>
  /** Drop local stream state (call from the selection-change event handler). */
  reset: () => void
  clearActionError: () => void
}

export function useAnalysisStream(authenticated: boolean): AnalysisStream {
  const [analysisId, setAnalysisId] = useState<string | null>(null)
  const [serverStatus, setServerStatus] = useState<ServerStatus | null>(null)
  const [connectionStatus, setConnectionStatus] =
    useState<ConnectionStatus>('idle')
  const [chunksReceived, setChunksReceived] = useState(0)
  const [partial, setPartial] = useState<PartialAnalysisResult | null>(null)
  const [result, setResult] = useState<AnalysisResult | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [lastEventId, setLastEventId] = useState<string | null>(null)

  // Mutable box via useState lazy init — avoids useRef(emptyBuffers()) re-create
  // and avoids mutating ref.current during render.
  const [buffersRef] = useState(() => ({ current: emptyBuffers() }))
  const lastEventIdRef = useRef<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const terminalRef = useRef(false)
  const reconnectFailuresRef = useRef(0)
  const analysisIdRef = useRef<string | null>(null)

  useEffect(() => {
    analysisIdRef.current = analysisId
  }, [analysisId])

  const uiState = useMemo(
    () =>
      deriveUiState({
        serverStatus,
        connectionStatus,
        chunksReceived,
        partial,
      }),
    [serverStatus, connectionStatus, chunksReceived, partial],
  )

  const display = result ?? partial

  function stopStream() {
    abortRef.current?.abort()
    abortRef.current = null
  }

  function applyChunk(field: string, delta: string) {
    if (!isAnalysisField(field)) return
    buffersRef.current[field] += delta
    setPartial(parseBuffers(buffersRef.current))
    setChunksReceived((n) => n + 1)
    setServerStatus((prev) =>
      prev === 'queued' || prev === null ? 'streaming' : prev,
    )
  }

  async function syncFromServer(id: string) {
    try {
      const analysis = await getAnalysis(id)
      setServerStatus(analysis.status)
      if (analysis.result) {
        setResult(analysis.result)
        setPartial(analysis.partial ?? analysis.result)
      } else if (analysis.partial) {
        buffersRef.current = buffersFromPartial(analysis.partial)
        setPartial(analysis.partial)
      }
      if (analysis.error) setErrorMessage(formatAnalysisError(analysis.error))
      if (
        analysis.status === 'done' ||
        analysis.status === 'error' ||
        analysis.status === 'cancelled'
      ) {
        terminalRef.current = true
      }
    } catch {
      /* best-effort sync */
    }
  }

  function connectStream(id: string) {
    stopStream()
    terminalRef.current = false
    reconnectFailuresRef.current = 0
    const controller = new AbortController()
    abortRef.current = controller

    void fetchEventSource(`/api/analyses/${id}/stream`, {
      method: 'GET',
      credentials: 'include',
      headers: {
        Accept: 'text/event-stream',
        // Library updates this key on each chunk id for subsequent retries.
        ...(lastEventIdRef.current
          ? { 'last-event-id': lastEventIdRef.current }
          : {}),
      },
      signal: controller.signal,
      openWhenHidden: true,
      async onopen(response) {
        if (response.ok) {
          setConnectionStatus('open')
          reconnectFailuresRef.current = 0
          if (lastEventIdRef.current) {
            await syncFromServer(id)
          }
          return
        }
        throw new Error(`Не удалось открыть SSE: HTTP ${response.status}`)
      },
      onmessage(ev) {
        if (ev.event === 'chunk') {
          if (ev.id) {
            lastEventIdRef.current = ev.id
            setLastEventId(ev.id)
          }
          try {
            const data = JSON.parse(ev.data) as {
              field?: string
              delta?: string
            }
            if (typeof data.field === 'string' && typeof data.delta === 'string') {
              applyChunk(data.field, data.delta)
            }
          } catch {
            /* ignore malformed chunk */
          }
          return
        }

        if (ev.event === 'done') {
          terminalRef.current = true
          setConnectionStatus('closed')
          setServerStatus('done')
          try {
            const data = JSON.parse(ev.data) as { result?: AnalysisResult }
            if (data.result) {
              setResult(data.result)
              setPartial(data.result)
            }
          } catch {
            /* keep accumulated partial */
          }
          stopStream()
          return
        }

        if (ev.event === 'error') {
          terminalRef.current = true
          setConnectionStatus('closed')
          setServerStatus('error')
          try {
            const data = JSON.parse(ev.data) as { message?: string }
            if (data.message) {
              setErrorMessage(formatAnalysisError(data.message) ?? data.message)
            }
          } catch {
            setErrorMessage(ev.data || 'ошибка стрима')
          }
          stopStream()
        }
      },
      onclose() {
        // Clean EOF: library does not auto-retry. Stay closed; sync if needed.
        if (terminalRef.current || controller.signal.aborted) {
          setConnectionStatus('closed')
          return
        }
        void syncFromServer(id).then(() => {
          if (!terminalRef.current) {
            setConnectionStatus('closed')
            setServerStatus((prev) =>
              prev === 'done' || prev === 'cancelled' || prev === 'error'
                ? prev
                : 'error',
            )
            setErrorMessage(
              'Стрим неожиданно закрыт; частичный результат сохранён',
            )
          }
        })
      },
      onerror(err) {
        if (terminalRef.current || controller.signal.aborted) {
          throw err
        }
        reconnectFailuresRef.current += 1
        setConnectionStatus('reconnecting')
        if (reconnectFailuresRef.current > MAX_RECONNECT_FAILURES) {
          setConnectionStatus('closed')
          setServerStatus((prev) =>
            prev === 'done' || prev === 'cancelled' ? prev : 'error',
          )
          setErrorMessage(
            `Не удалось переподключиться после ${MAX_RECONNECT_FAILURES} попыток; частичный результат сохранён`,
          )
          throw err
        }
        // Use server `retry:` interval (library default) when we return void.
        return DEFAULT_RETRY_MS
      },
    })
  }

  function resetLocalState() {
    stopStream()
    terminalRef.current = false
    reconnectFailuresRef.current = 0
    buffersRef.current = emptyBuffers()
    lastEventIdRef.current = null
    setLastEventId(null)
    setPartial(null)
    setResult(null)
    setChunksReceived(0)
    setConnectionStatus('idle')
    setAnalysisId(null)
    setServerStatus(null)
    setErrorMessage(null)
    setActionError(null)
  }

  async function start(callAttemptId: string) {
    setActionError(null)
    setErrorMessage(null)
    if (!authenticated) {
      setActionError('Сначала выпустите dev-токен')
      return
    }
    if (!callAttemptId.trim()) {
      setActionError('Укажите call_attempt_id')
      return
    }

    resetLocalState()

    try {
      const created = await createAnalysis(callAttemptId.trim())
      setAnalysisId(created.id)
      setServerStatus(created.status)
      connectStream(created.id)
    } catch (err) {
      setActionError(analysisActionMessage(err))
      setServerStatus(null)
    }
  }

  async function cancel() {
    setActionError(null)
    const id = analysisIdRef.current
    if (!id) return
    try {
      const cancelled = await cancelAnalysis(id)
      terminalRef.current = true
      setServerStatus(cancelled.status)
      setConnectionStatus('closed')
      stopStream()
    } catch (err) {
      setActionError(analysisActionMessage(err))
    }
  }

  useEffect(() => () => stopStream(), [])

  const busy =
    serverStatus === 'queued' ||
    serverStatus === 'streaming' ||
    connectionStatus === 'reconnecting' ||
    connectionStatus === 'open'

  return {
    uiState,
    analysisId,
    serverStatus,
    connectionStatus,
    lastEventId,
    display,
    errorMessage,
    actionError,
    busy,
    start,
    cancel,
    reset: resetLocalState,
    clearActionError: () => setActionError(null),
  }
}
