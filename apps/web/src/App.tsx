import { useEffect, useState } from 'react'
import { AnalysisPanel } from './components/AnalysisPanel'
import { AnalysisResultView } from './components/AnalysisResult'
import { CallsPanel } from './components/CallsPanel'
import { DevTokenForm } from './components/DevTokenForm'
import { getDevSession, type DevSession } from './lib/api'
import { useAnalysisStream } from './lib/useAnalysisStream'
import type { UiState } from './lib/uiState'

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

export default function App() {
  const [session, setSession] = useState<DevSession | null>(null)
  const [sessionLoading, setSessionLoading] = useState(true)
  const [callAttemptId, setCallAttemptId] = useState('')
  const [shellError, setShellError] = useState<string | null>(null)

  const authenticated = Boolean(session?.authenticated)
  const stream = useAnalysisStream(authenticated)

  useEffect(() => {
    let cancelled = false
    void getDevSession()
      .then((s) => {
        if (!cancelled) setSession(s)
      })
      .catch((err) => {
        if (!cancelled) {
          setShellError(err instanceof Error ? err.message : String(err))
          setSession({
            authenticated: false,
            sub: null,
            org_id: null,
            role: null,
          })
        }
      })
      .finally(() => {
        if (!cancelled) setSessionLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const actionError = shellError ?? stream.actionError

  return (
    <div
      data-state={stream.uiState}
      className="min-h-screen bg-slate-50 text-slate-900"
    >
      <div className="mx-auto max-w-4xl space-y-6 p-6">
        <header className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">
            Calls & analysis
          </h1>
          <span
            className={`rounded px-2 py-1 text-xs font-mono uppercase tracking-wide ${STATE_BADGE[stream.uiState]}`}
            title="data-state"
          >
            data-state: {stream.uiState}
          </span>
        </header>

        <DevTokenForm
          session={session}
          sessionLoading={sessionLoading}
          onSessionChange={setSession}
          onError={(msg) => {
            setShellError(msg)
            stream.clearActionError()
          }}
        />

        {authenticated ? (
          <CallsPanel
            selectedId={callAttemptId || null}
            onSelect={(id) => setCallAttemptId(id)}
          />
        ) : null}

        <AnalysisPanel
          callAttemptId={callAttemptId}
          onCallAttemptIdChange={setCallAttemptId}
          analysisId={stream.analysisId}
          serverStatus={stream.serverStatus}
          connectionStatus={stream.connectionStatus}
          lastEventId={stream.lastEventId}
          busy={stream.busy}
          authenticated={authenticated}
          actionError={actionError}
          errorMessage={stream.errorMessage}
          onStart={() => {
            setShellError(null)
            void stream.start(callAttemptId)
          }}
          onCancel={() => {
            setShellError(null)
            void stream.cancel()
          }}
        />

        <AnalysisResultView display={stream.display} />
      </div>
    </div>
  )
}
