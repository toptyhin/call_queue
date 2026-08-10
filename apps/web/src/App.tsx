import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from 'react'
import { fetchEventSource } from '@microsoft/fetch-event-source'
import type { AnalysisResult, PartialAnalysisResult } from './generated/analysis-result'
import {
  cancelAnalysis,
  createAnalysis,
  getAnalysis,
  mintDevToken,
} from './lib/api'
import {
  buffersFromPartial,
  emptyBuffers,
  isAnalysisField,
  parseBuffers,
  type FieldBuffers,
} from './lib/partial'
import {
  deriveUiState,
  type ConnectionStatus,
  type ServerStatus,
  type UiState,
} from './lib/uiState'

const TOKEN_KEY = 'sound.dev_token'
const MAX_RECONNECT_FAILURES = 5
const DEFAULT_RETRY_MS = 3000

const STATE_BADGE: Record<UiState, string> = {
  idle: 'bg-slate-200 text-slate-800',
  queued: 'bg-amber-200 text-amber-900',
  streaming: 'bg-sky-200 text-sky-900',
  reconnecting: 'bg-orange-200 text-orange-900',
  done: 'bg-emerald-200 text-emerald-900',
  partial: 'bg-violet-200 text-violet-900',
  error: 'bg-red-200 text-red-900',
  cancelled: 'bg-zinc-300 text-zinc-800',
}

function loadStoredToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? ''
  } catch {
    return ''
  }
}

export default function App() {
  const [sub, setSub] = useState('dev-user')
  const [orgId, setOrgId] = useState('00000000-0000-4000-8000-000000000001')
  const [token, setToken] = useState(loadStoredToken)
  const [callAttemptId, setCallAttemptId] = useState('')

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

  const buffersRef = useRef<FieldBuffers>(emptyBuffers())
  const lastEventIdRef = useRef<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const terminalRef = useRef(false)
  const reconnectFailuresRef = useRef(0)
  const tokenRef = useRef(token)
  const analysisIdRef = useRef<string | null>(null)

  useEffect(() => {
    tokenRef.current = token
  }, [token])

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

  function persistToken(value: string) {
    setToken(value)
    try {
      if (value) localStorage.setItem(TOKEN_KEY, value)
      else localStorage.removeItem(TOKEN_KEY)
    } catch {
      /* ignore quota / private mode */
    }
  }

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

  async function syncFromServer(id: string, bearer: string) {
    try {
      const analysis = await getAnalysis(bearer, id)
      setServerStatus(analysis.status)
      if (analysis.result) {
        setResult(analysis.result)
        setPartial(analysis.partial ?? analysis.result)
      } else if (analysis.partial) {
        buffersRef.current = buffersFromPartial(analysis.partial)
        setPartial(analysis.partial)
      }
      if (analysis.error) setErrorMessage(analysis.error)
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

  function connectStream(id: string, bearer: string) {
    stopStream()
    terminalRef.current = false
    reconnectFailuresRef.current = 0
    const controller = new AbortController()
    abortRef.current = controller

    void fetchEventSource(`/api/analyses/${id}/stream`, {
      method: 'GET',
      headers: {
        Authorization: `Bearer ${bearer}`,
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
            await syncFromServer(id, bearer)
          }
          return
        }
        throw new Error(`SSE open failed: HTTP ${response.status}`)
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
            if (data.message) setErrorMessage(data.message)
          } catch {
            setErrorMessage(ev.data || 'stream error')
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
        void syncFromServer(id, bearer).then(() => {
          if (!terminalRef.current) {
            setConnectionStatus('closed')
            setServerStatus((prev) =>
              prev === 'done' || prev === 'cancelled' || prev === 'error'
                ? prev
                : 'error',
            )
            setErrorMessage('Stream closed unexpectedly; keeping partial')
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
            `Reconnect failed after ${MAX_RECONNECT_FAILURES} attempts; keeping partial`,
          )
          throw err
        }
        // Use server `retry:` interval (library default) when we return void.
        return DEFAULT_RETRY_MS
      },
    })
  }

  async function handleMintToken(e: FormEvent) {
    e.preventDefault()
    setActionError(null)
    try {
      const { token: minted } = await mintDevToken({
        sub,
        org_id: orgId,
        role: 'authenticated',
      })
      persistToken(minted)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    }
  }

  async function handleStart() {
    setActionError(null)
    setErrorMessage(null)
    if (!token) {
      setActionError('Mint a dev token first')
      return
    }
    if (!callAttemptId.trim()) {
      setActionError('call_attempt_id is required')
      return
    }

    stopStream()
    buffersRef.current = emptyBuffers()
    lastEventIdRef.current = null
    setLastEventId(null)
    setPartial(null)
    setResult(null)
    setChunksReceived(0)
    setConnectionStatus('idle')
    setAnalysisId(null)
    setServerStatus(null)

    try {
      const created = await createAnalysis(token, callAttemptId.trim())
      setAnalysisId(created.id)
      setServerStatus(created.status)
      connectStream(created.id, token)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
      setServerStatus(null)
    }
  }

  async function handleCancel() {
    setActionError(null)
    const id = analysisIdRef.current
    const bearer = tokenRef.current
    if (!id || !bearer) return
    try {
      const cancelled = await cancelAnalysis(bearer, id)
      terminalRef.current = true
      setServerStatus(cancelled.status)
      setConnectionStatus('closed')
      stopStream()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    }
  }

  useEffect(() => () => stopStream(), [])

  const busy =
    serverStatus === 'queued' ||
    serverStatus === 'streaming' ||
    connectionStatus === 'reconnecting' ||
    connectionStatus === 'open'

  return (
    <div
      data-state={uiState}
      className="min-h-screen bg-slate-50 text-slate-900"
    >
      <div className="mx-auto max-w-3xl space-y-6 p-6">
        <header className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">
            Call analysis
          </h1>
          <span
            className={`rounded px-2 py-1 text-xs font-mono uppercase tracking-wide ${STATE_BADGE[uiState]}`}
            title="data-state"
          >
            data-state: {uiState}
          </span>
        </header>

        <section className="space-y-3 rounded border border-slate-200 bg-white p-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Dev token
          </h2>
          <form
            onSubmit={handleMintToken}
            className="grid gap-3 sm:grid-cols-2"
          >
            <label className="block text-sm">
              <span className="mb-1 block text-slate-600">sub</span>
              <input
                className="w-full rounded border border-slate-300 px-2 py-1.5"
                value={sub}
                onChange={(e) => setSub(e.target.value)}
                required
              />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block text-slate-600">org_id</span>
              <input
                className="w-full rounded border border-slate-300 px-2 py-1.5 font-mono text-sm"
                value={orgId}
                onChange={(e) => setOrgId(e.target.value)}
                required
              />
            </label>
            <div className="sm:col-span-2 flex flex-wrap items-center gap-3">
              <button
                type="submit"
                className="rounded bg-slate-800 px-3 py-1.5 text-sm text-white hover:bg-slate-700"
              >
                Mint token
              </button>
              <span className="text-xs text-slate-500">
                role: authenticated · stored in localStorage
              </span>
            </div>
          </form>
          {token ? (
            <p className="break-all rounded bg-slate-100 p-2 font-mono text-xs text-slate-700">
              {token}
            </p>
          ) : (
            <p className="text-sm text-slate-500">No token yet</p>
          )}
        </section>

        <section className="space-y-3 rounded border border-slate-200 bg-white p-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Analysis
          </h2>
          <label className="block text-sm">
            <span className="mb-1 block text-slate-600">call_attempt_id</span>
            <input
              className="w-full rounded border border-slate-300 px-2 py-1.5 font-mono text-sm"
              value={callAttemptId}
              onChange={(e) => setCallAttemptId(e.target.value)}
              placeholder="uuid"
            />
          </label>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void handleStart()}
              disabled={!token || busy}
              className="rounded bg-sky-700 px-3 py-1.5 text-sm text-white hover:bg-sky-600 disabled:opacity-40"
            >
              Start analysis
            </button>
            <button
              type="button"
              onClick={() => void handleCancel()}
              disabled={!analysisId || !busy}
              className="rounded border border-slate-300 bg-white px-3 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-40"
            >
              Cancel
            </button>
          </div>
          <dl className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
            <div>
              <dt className="text-slate-500">Server status</dt>
              <dd className="font-mono">{serverStatus ?? '—'}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Connection</dt>
              <dd className="font-mono">{connectionStatus}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Analysis id</dt>
              <dd className="truncate font-mono text-xs">
                {analysisId ?? '—'}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">Last-Event-ID</dt>
              <dd className="font-mono">{lastEventId ?? '—'}</dd>
            </div>
          </dl>
          {actionError ? (
            <p className="text-sm text-red-700">{actionError}</p>
          ) : null}
          {errorMessage ? (
            <p className="text-sm text-red-700">Error: {errorMessage}</p>
          ) : null}
        </section>

        <section className="space-y-3 rounded border border-slate-200 bg-white p-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Result / partial
          </h2>
          <Field label="summary" value={display?.summary} />
          <Field
            label="objections"
            value={
              display?.objections !== undefined
                ? JSON.stringify(display.objections, null, 2)
                : undefined
            }
            mono
          />
          <Field label="next_step" value={display?.next_step} mono />
          <Field
            label="lead_score"
            value={
              display?.lead_score !== undefined
                ? String(display.lead_score)
                : undefined
            }
            mono
          />
          <Field
            label="confidence"
            value={
              display?.confidence !== undefined
                ? String(display.confidence)
                : undefined
            }
            mono
          />
        </section>
      </div>
    </div>
  )
}

function Field({
  label,
  value,
  mono,
}: {
  label: string
  value: string | undefined
  mono?: boolean
}) {
  return (
    <div>
      <div className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
      </div>
      <pre
        className={`min-h-[2rem] whitespace-pre-wrap rounded bg-slate-50 p-2 text-sm ${
          mono ? 'font-mono' : ''
        } ${value === undefined ? 'text-slate-400' : 'text-slate-800'}`}
      >
        {value === undefined ? '—' : value}
      </pre>
    </div>
  )
}
