import { useEffect, useState } from 'react'
import { AnalysisPanel } from './components/AnalysisPanel'
import { AnalysisResultView } from './components/AnalysisResult'
import { CallsPanel } from './components/CallsPanel'
import { DevTokenForm } from './components/DevTokenForm'
import { getDevSession, type DevSession } from './lib/api'
import { useAnalysisStream } from './lib/useAnalysisStream'

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

  function selectCallAttempt(id: string) {
    if (id !== callAttemptId) {
      stream.reset()
      setShellError(null)
    }
    setCallAttemptId(id)
  }

  const actionError = shellError ?? stream.actionError

  return (
    <div
      data-state={stream.uiState}
      className="min-h-screen bg-slate-50 text-slate-900"
    >
      <div className="mx-auto max-w-4xl space-y-6 p-6">
        <header className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">
            Звонки и разбор
          </h1>
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
            onSelect={(id) => selectCallAttempt(id ?? '')}
          >
            {({
              attemptStatus,
              hasCompletedAnalysis,
              analysesKnown,
              completedDisplay,
            }) => {
              const uiState =
                hasCompletedAnalysis && stream.uiState === 'idle'
                  ? 'done'
                  : stream.uiState
              const serverStatus =
                hasCompletedAnalysis && stream.serverStatus == null
                  ? 'done'
                  : stream.serverStatus
              return (
                <>
                  <AnalysisPanel
                    embedded
                    callAttemptId={callAttemptId}
                    onCallAttemptIdChange={selectCallAttempt}
                    attemptStatus={attemptStatus}
                    hasCompletedAnalysis={hasCompletedAnalysis}
                    analysesKnown={analysesKnown}
                    uiState={uiState}
                    analysisId={stream.analysisId}
                    serverStatus={serverStatus}
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
                  <AnalysisResultView
                    embedded
                    display={stream.display ?? completedDisplay}
                  />
                </>
              )
            }}
          </CallsPanel>
        ) : null}
      </div>
    </div>
  )
}
