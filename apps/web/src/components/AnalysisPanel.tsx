import type { CallAttemptStatus } from '../lib/api'
import type { ConnectionStatus, ServerStatus, UiState } from '../lib/uiState'

const CONNECTION_LABEL: Record<ConnectionStatus, string> = {
  idle: 'ожидание',
  open: 'открыто',
  reconnecting: 'переподключение',
  closed: 'закрыто',
}

const SERVER_STATUS_LABEL: Record<ServerStatus, string> = {
  queued: 'в очереди',
  streaming: 'стрим',
  done: 'готово',
  error: 'ошибка',
  cancelled: 'отменён',
}

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

type Props = {
  callAttemptId: string
  onCallAttemptIdChange: (value: string) => void
  /** Status of the selected call attempt; gates the start button. */
  attemptStatus: CallAttemptStatus | null
  /** Detail loaded for this selection — needed before trusting analyses. */
  analysesKnown: boolean
  /** True when the attempt already has a `done` analysis — do not offer a new run. */
  hasCompletedAnalysis: boolean
  uiState: UiState
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
  /** Flatter layout when nested under a call row. */
  embedded?: boolean
}

export function AnalysisPanel({
  callAttemptId,
  onCallAttemptIdChange,
  attemptStatus,
  analysesKnown,
  hasCompletedAnalysis,
  uiState,
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
  embedded = false,
}: Props) {
  const notCompleted =
    attemptStatus != null && attemptStatus !== 'completed'
  const analysisReady = hasCompletedAnalysis || serverStatus === 'done'
  const startDisabled =
    !authenticated ||
    busy ||
    !callAttemptId ||
    notCompleted ||
    analysisReady ||
    !analysesKnown

  return (
    <section
      data-state={uiState}
      className={
        embedded
          ? 'space-y-3 border-t border-slate-200 pt-3'
          : 'space-y-3 rounded border border-slate-200 bg-white p-4'
      }
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Разбор
        </h2>
        <span
          className={`rounded px-2 py-1 text-xs font-mono uppercase tracking-wide ${STATE_BADGE[uiState]}`}
          title="data-state"
        >
          data-state: {uiState}
        </span>
      </div>
      {embedded ? null : (
        <label className="block text-sm">
          <span className="mb-1 block text-slate-600">call_attempt_id</span>
          <input
            className="w-full rounded border border-slate-300 px-2 py-1.5 font-mono text-sm"
            value={callAttemptId}
            onChange={(e) => onCallAttemptIdChange(e.target.value)}
            placeholder="uuid"
          />
        </label>
      )}
      {analysisReady ? (
        <p className="text-sm text-emerald-800">
          Готовый разбор уже есть — повторный запуск не предлагается.
        </p>
      ) : (
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={onStart}
            disabled={startDisabled}
            title={
              notCompleted
                ? 'Разбор доступен только для завершённых звонков'
                : !analysesKnown
                  ? 'Загрузка деталей звонка…'
                  : undefined
            }
            className="rounded bg-sky-700 px-3 py-1.5 text-sm text-white hover:bg-sky-600 disabled:opacity-40"
          >
            Запустить разбор
          </button>
          <button
            type="button"
            onClick={onCancel}
            disabled={!analysisId || !busy}
            className="rounded border border-slate-300 bg-white px-3 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-40"
          >
            Отменить
          </button>
        </div>
      )}
      {notCompleted && !analysisReady ? (
        <p className="text-sm text-amber-800">
          Разбор доступен только для завершённых звонков
          {attemptStatus ? ` (сейчас: ${attemptStatus})` : ''}.
        </p>
      ) : null}
      <dl className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
        <div>
          <dt className="text-slate-500">Статус сервера</dt>
          <dd className="font-mono">
            {serverStatus ? SERVER_STATUS_LABEL[serverStatus] : '—'}
          </dd>
        </div>
        <div>
          <dt className="text-slate-500">Соединение</dt>
          <dd className="font-mono">{CONNECTION_LABEL[connectionStatus]}</dd>
        </div>
        <div>
          <dt className="text-slate-500">ID разбора</dt>
          <dd className="truncate font-mono text-xs">{analysisId ?? '—'}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Last-Event-ID</dt>
          <dd className="font-mono">{lastEventId ?? '—'}</dd>
        </div>
      </dl>
      {actionError ? <p className="text-sm text-red-700">{actionError}</p> : null}
      {errorMessage ? (
        <p className="text-sm text-red-700">Ошибка: {errorMessage}</p>
      ) : null}
    </section>
  )
}
