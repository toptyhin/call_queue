import type { ConnectionStatus, ServerStatus } from '../lib/uiState'

type Props = {
  callAttemptId: string
  onCallAttemptIdChange: (value: string) => void
  analysisId: string | null
  serverStatus: ServerStatus | null
  connectionStatus: ConnectionStatus
  lastEventId: string | null
  busy: boolean
  authenticated: boolean
  actionError: string | null
  errorMessage: string | null
  onStart: () => void
  onCancel: () => void
}

export function AnalysisPanel({
  callAttemptId,
  onCallAttemptIdChange,
  analysisId,
  serverStatus,
  connectionStatus,
  lastEventId,
  busy,
  authenticated,
  actionError,
  errorMessage,
  onStart,
  onCancel,
}: Props) {
  return (
    <section className="space-y-3 rounded border border-slate-200 bg-white p-4">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
        Analysis
      </h2>
      <label className="block text-sm">
        <span className="mb-1 block text-slate-600">call_attempt_id</span>
        <input
          className="w-full rounded border border-slate-300 px-2 py-1.5 font-mono text-sm"
          value={callAttemptId}
          onChange={(e) => onCallAttemptIdChange(e.target.value)}
          placeholder="uuid"
        />
      </label>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={onStart}
          disabled={!authenticated || busy}
          className="rounded bg-sky-700 px-3 py-1.5 text-sm text-white hover:bg-sky-600 disabled:opacity-40"
        >
          Start analysis
        </button>
        <button
          type="button"
          onClick={onCancel}
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
          <dd className="truncate font-mono text-xs">{analysisId ?? '—'}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Last-Event-ID</dt>
          <dd className="font-mono">{lastEventId ?? '—'}</dd>
        </div>
      </dl>
      {actionError ? <p className="text-sm text-red-700">{actionError}</p> : null}
      {errorMessage ? (
        <p className="text-sm text-red-700">Error: {errorMessage}</p>
      ) : null}
    </section>
  )
}
