import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { fetchEventSource } from '@microsoft/fetch-event-source'
import type {
  AnalysisResult,
  PartialAnalysisResult,
} from '../generated/analysis-result'
import {
  getCallAttempt,
  listCallAttempts,
  type CallAttemptDetail,
  type CallAttemptListItem,
  type CallAttemptStatus,
  type CallFeedAnalysisEvent,
  type CallFeedCrmEvent,
  type CrmDelivery,
  type StatusHistoryItem,
} from '../lib/api'
import { formatAnalysisError, formatCrmLastError } from '../lib/formatError'

const PAGE_SIZE = 20

const STATUSES: CallAttemptStatus[] = [
  'queued',
  'dialing',
  'in_progress',
  'completed',
  'failed',
  'no_answer',
]

const STATUS_LABEL: Record<CallAttemptStatus, string> = {
  queued: 'в очереди',
  dialing: 'набор',
  in_progress: 'идёт',
  completed: 'завершён',
  failed: 'ошибка',
  no_answer: 'не ответил',
}

const STATUS_BADGE: Record<string, string> = {
  queued: 'bg-slate-200 text-slate-800',
  dialing: 'bg-amber-200 text-amber-900',
  in_progress: 'bg-sky-200 text-sky-900',
  completed: 'bg-emerald-200 text-emerald-900',
  failed: 'bg-red-200 text-red-900',
  no_answer: 'bg-zinc-300 text-zinc-800',
}

const CRM_LABEL: Record<string, string> = {
  pending: 'ожидает',
  retrying: 'повтор',
  delivered: 'доставлено',
}

const CRM_BADGE: Record<string, string> = {
  pending: 'bg-amber-100 text-amber-900',
  retrying: 'bg-orange-100 text-orange-900',
  delivered: 'bg-emerald-100 text-emerald-900',
}

const LLM_LABEL: Record<string, string> = {
  queued: 'в очереди',
  streaming: 'стрим',
  done: 'готово',
  error: 'ошибка',
  cancelled: 'отменён',
}

const LLM_BADGE: Record<string, string> = {
  queued: 'bg-amber-100 text-amber-900',
  streaming: 'bg-sky-100 text-sky-900',
  done: 'bg-emerald-100 text-emerald-900',
  error: 'bg-red-100 text-red-900',
  cancelled: 'bg-zinc-200 text-zinc-800',
}

const FEED_STATUS_LABEL: Record<'idle' | 'open' | 'reconnecting', string> = {
  idle: 'ожидание',
  open: 'открыта',
  reconnecting: 'переподключение',
}

const SOURCE_LABEL: Record<string, string> = {
  claim: 'выдача',
  webhook: 'вебхук',
  abort: 'abort',
}

type ListFilters = {
  status: CallAttemptStatus | ''
  phone: string
  createdFrom: string
  createdTo: string
}

function fmtTime(value: string | null | undefined): string {
  if (!value) return '—'
  try {
    return new Date(value).toLocaleString('ru-RU')
  } catch {
    return value
  }
}

function historyKey(h: StatusHistoryItem): string {
  return [
    h.source,
    h.status,
    h.sequence == null ? 'noseq' : String(h.sequence),
    h.event_type ?? 'notype',
    h.at,
  ].join(':')
}

/** Convert datetime-local value to ISO for the API; empty → undefined. */
function localToIso(value: string): string | undefined {
  if (!value) return undefined
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return undefined
  return d.toISOString()
}

function sanitizePhonePrefix(raw: string): string | undefined {
  const trimmed = raw.trim()
  if (!trimmed) return undefined
  if (!/^\+?[0-9]{1,15}$/.test(trimmed)) return undefined
  return trimmed
}

function matchesFilters(item: CallAttemptListItem, filters: ListFilters): boolean {
  if (filters.status && item.status !== filters.status) return false
  const phone = sanitizePhonePrefix(filters.phone)
  if (phone && !item.phone.startsWith(phone)) return false
  const fromIso = localToIso(filters.createdFrom)
  if (fromIso && new Date(item.created_at) < new Date(fromIso)) return false
  const toIso = localToIso(filters.createdTo)
  if (toIso && new Date(item.created_at) >= new Date(toIso)) return false
  return true
}

function upsertAttempt(
  prev: CallAttemptListItem[],
  item: CallAttemptListItem,
): CallAttemptListItem[] {
  const idx = prev.findIndex((x) => x.id === item.id)
  if (idx === -1) return [item, ...prev]
  return prev.map((x, i) => (i === idx ? item : x))
}

function removeAttempt(
  prev: CallAttemptListItem[],
  attemptId: string,
): CallAttemptListItem[] {
  return prev.filter((x) => x.id !== attemptId)
}

type CallsPanelChildProps = {
  attemptStatus: CallAttemptStatus | null
  /** Detail for the selection is loaded (analyses list is trustworthy). */
  analysesKnown: boolean
  /** Selected attempt already has at least one analysis in `done`. */
  hasCompletedAnalysis: boolean
  /** Latest `done` analysis payload for the result panel (if any). */
  completedDisplay: AnalysisResult | PartialAnalysisResult | null
}

type Props = {
  selectedId: string | null
  onSelect: (attemptId: string | null) => void
  /** Rendered under the selected row (call detail is always shown above). */
  children?: (ctx: CallsPanelChildProps) => ReactNode
}

/** Enter-only height expand; unmounts instantly when parent stops rendering it. */
function SlideDown({ children }: { children: ReactNode }) {
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    // Double rAF so the browser paints grid-rows-[0fr] before expanding.
    let inner = 0
    const outer = requestAnimationFrame(() => {
      inner = requestAnimationFrame(() => setExpanded(true))
    })
    return () => {
      cancelAnimationFrame(outer)
      cancelAnimationFrame(inner)
    }
  }, [])

  return (
    <div
      className={`grid transition-[grid-template-rows] duration-300 ease-out ${
        expanded ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'
      }`}
    >
      <div className="min-h-0 overflow-hidden">{children}</div>
    </div>
  )
}

export function CallsPanel({ selectedId, onSelect, children }: Props) {
  const [items, setItems] = useState<CallAttemptListItem[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [detail, setDetail] = useState<CallAttemptDetail | null>(null)
  const [listError, setListError] = useState<string | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [listLoading, setListLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [feedStatus, setFeedStatus] = useState<'idle' | 'open' | 'reconnecting'>(
    'idle',
  )

  const [status, setStatus] = useState<CallAttemptStatus | ''>('')
  const [phoneInput, setPhoneInput] = useState('')
  const [debouncedPhone, setDebouncedPhone] = useState('')
  const [createdFrom, setCreatedFrom] = useState('')
  const [createdTo, setCreatedTo] = useState('')

  const filters = useMemo<ListFilters>(
    () => ({
      status,
      phone: debouncedPhone,
      createdFrom,
      createdTo,
    }),
    [status, debouncedPhone, createdFrom, createdTo],
  )
  const filtersRef = useRef(filters)
  const selectedRef = useRef(selectedId)
  const crmOverrideRef = useRef<Record<string, CrmDelivery | null>>({})
  const detailReqRef = useRef(0)
  const listReqRef = useRef(0)

  useEffect(() => {
    selectedRef.current = selectedId
  }, [selectedId])

  useEffect(() => {
    filtersRef.current = filters
  }, [filters])

  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedPhone(phoneInput.trim()), 300)
    return () => window.clearTimeout(t)
  }, [phoneInput])

  const queryOpts = useCallback(
    (cursor?: string | null) => ({
      limit: PAGE_SIZE,
      cursor: cursor ?? undefined,
      status: filters.status || undefined,
      phone: sanitizePhonePrefix(filters.phone),
      createdFrom: localToIso(filters.createdFrom),
      createdTo: localToIso(filters.createdTo),
    }),
    [filters],
  )

  const refreshList = useCallback(async () => {
    const reqId = ++listReqRef.current
    setListError(null)
    setListLoading(true)
    try {
      const page = await listCallAttempts(queryOpts())
      if (reqId !== listReqRef.current) return
      setItems(page.items)
      setNextCursor(page.next_cursor)
    } catch (err) {
      if (reqId !== listReqRef.current) return
      setListError(err instanceof Error ? err.message : String(err))
    } finally {
      if (reqId === listReqRef.current) setListLoading(false)
    }
  }, [queryOpts])

  const loadMore = useCallback(async () => {
    if (!nextCursor || loadingMore) return
    const reqId = ++listReqRef.current
    setListError(null)
    setLoadingMore(true)
    try {
      const page = await listCallAttempts(queryOpts(nextCursor))
      if (reqId !== listReqRef.current) return
      setItems((prev) => {
        const seen = new Set(prev.map((x) => x.id))
        const appended = page.items.filter((x) => !seen.has(x.id))
        return [...prev, ...appended]
      })
      setNextCursor(page.next_cursor)
    } catch (err) {
      if (reqId !== listReqRef.current) return
      setListError(err instanceof Error ? err.message : String(err))
    } finally {
      if (reqId === listReqRef.current) setLoadingMore(false)
    }
  }, [nextCursor, loadingMore, queryOpts])

  const refreshDetail = useCallback(async (id: string) => {
    const reqId = ++detailReqRef.current
    setDetailError(null)
    try {
      const d = await getCallAttempt(id)
      if (reqId !== detailReqRef.current || selectedRef.current !== id) {
        return
      }
      const override = crmOverrideRef.current[id]
      if (override !== undefined) {
        d.crm = override
      }
      setDetail(d)
    } catch (err) {
      if (reqId !== detailReqRef.current || selectedRef.current !== id) {
        return
      }
      // Keep previous detail; only surface the error for the active selection.
      setDetailError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  useEffect(() => {
    void refreshList()
  }, [refreshList])

  useEffect(() => {
    if (!selectedId) {
      detailReqRef.current += 1
      setDetail(null)
      setDetailError(null)
      return
    }
    void refreshDetail(selectedId)
  }, [selectedId, refreshDetail])

  useEffect(() => {
    const controller = new AbortController()
    setFeedStatus('reconnecting')

    void fetchEventSource('/api/call_attempts/stream', {
      method: 'GET',
      credentials: 'include',
      headers: {
        Accept: 'text/event-stream',
      },
      signal: controller.signal,
      openWhenHidden: true,
      async onopen(response) {
        if (!response.ok) {
          throw new Error(
            `Не удалось открыть SSE ленты звонков: HTTP ${response.status}`,
          )
        }
        setFeedStatus('open')
      },
      onmessage(ev) {
        if (ev.event === 'attempt') {
          try {
            const item = JSON.parse(ev.data) as CallAttemptListItem
            if (matchesFilters(item, filtersRef.current)) {
              setItems((prev) => upsertAttempt(prev, item))
            } else {
              setItems((prev) => removeAttempt(prev, item.id))
            }
            if (selectedRef.current === item.id) {
              void refreshDetail(item.id)
            }
          } catch {
            /* ignore */
          }
          return
        }

        if (ev.event === 'crm') {
          try {
            const data = JSON.parse(ev.data) as CallFeedCrmEvent
            crmOverrideRef.current[data.attempt_id] = data.crm
            if (selectedRef.current === data.attempt_id) {
              setDetail((prev) =>
                prev && prev.id === data.attempt_id
                  ? { ...prev, crm: data.crm }
                  : prev,
              )
            }
          } catch {
            /* ignore */
          }
          return
        }

        if (ev.event === 'analysis') {
          try {
            const data = JSON.parse(ev.data) as CallFeedAnalysisEvent
            if (selectedRef.current === data.attempt_id) {
              void refreshDetail(data.attempt_id)
            }
          } catch {
            /* ignore */
          }
        }
      },
      onerror() {
        setFeedStatus('reconnecting')
        return 3000
      },
    })

    return () => controller.abort()
  }, [refreshDetail])

  const phoneInvalid =
    phoneInput.trim().length > 0 && sanitizePhonePrefix(phoneInput) === undefined

  const clearFilters = () => {
    setStatus('')
    setPhoneInput('')
    setDebouncedPhone('')
    setCreatedFrom('')
    setCreatedTo('')
  }

  const hasActiveFilters =
    status !== '' ||
    debouncedPhone !== '' ||
    createdFrom !== '' ||
    createdTo !== ''

  const selectedAttemptStatus = useMemo((): CallAttemptStatus | null => {
    if (!selectedId) return null
    const fromList = items.find((x) => x.id === selectedId)
    if (fromList) return fromList.status
    if (detail?.id === selectedId) return detail.status
    return null
  }, [selectedId, items, detail])

  const analysesKnown = Boolean(
    selectedId && detail && detail.id === selectedId,
  )
  const completedAnalysis = analysesKnown
    ? (detail?.analyses.find((a) => a.status === 'done') ?? null)
    : null
  const hasCompletedAnalysis = completedAnalysis != null
  const completedDisplay =
    completedAnalysis?.result ?? completedAnalysis?.partial ?? null

  return (
    <section className="space-y-3 rounded border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Звонки
        </h2>
        <span className="font-mono text-xs text-slate-500">
          лента: {FEED_STATUS_LABEL[feedStatus]}
        </span>
      </div>

      <div className="grid gap-2 rounded border border-slate-100 bg-slate-50 p-3 sm:grid-cols-2 lg:grid-cols-4">
        <label className="block text-xs text-slate-600">
          Статус
          <select
            value={status}
            onChange={(e) =>
              setStatus(e.target.value as CallAttemptStatus | '')
            }
            className="mt-1 w-full rounded border border-slate-200 bg-white px-2 py-1.5 font-mono text-xs"
          >
            <option value="">все</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABEL[s]}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-xs text-slate-600">
          Префикс телефона
          <input
            type="text"
            inputMode="tel"
            placeholder="+7495…"
            value={phoneInput}
            onChange={(e) => setPhoneInput(e.target.value)}
            className={`mt-1 w-full rounded border bg-white px-2 py-1.5 font-mono text-xs ${
              phoneInvalid ? 'border-red-400' : 'border-slate-200'
            }`}
          />
          {phoneInvalid ? (
            <span className="mt-0.5 block text-[10px] text-red-600">
              только + и цифры (≤15)
            </span>
          ) : null}
        </label>
        <label className="block text-xs text-slate-600">
          Создан с
          <input
            type="datetime-local"
            value={createdFrom}
            onChange={(e) => setCreatedFrom(e.target.value)}
            className="mt-1 w-full rounded border border-slate-200 bg-white px-2 py-1.5 font-mono text-xs"
          />
        </label>
        <label className="block text-xs text-slate-600">
          Создан по
          <input
            type="datetime-local"
            value={createdTo}
            onChange={(e) => setCreatedTo(e.target.value)}
            className="mt-1 w-full rounded border border-slate-200 bg-white px-2 py-1.5 font-mono text-xs"
          />
        </label>
        {hasActiveFilters ? (
          <div className="sm:col-span-2 lg:col-span-4">
            <button
              type="button"
              onClick={clearFilters}
              className="rounded border border-slate-200 bg-white px-2 py-1 text-xs text-slate-600 hover:bg-slate-100"
            >
              Сбросить фильтры
            </button>
          </div>
        ) : null}
      </div>

      {listError ? (
        <p className="text-sm text-red-700">{listError}</p>
      ) : null}

      {items.length === 0 && !listError && !listLoading ? (
        <p className="text-sm text-slate-500">
          {hasActiveFilters ? (
            <>Нет попыток звонка по фильтрам.</>
          ) : (
            <>
              Попыток звонка пока нет. Выполните{' '}
              <code className="font-mono">make seed</code> или claim контакта.
            </>
          )}
        </p>
      ) : (
        <ul className="divide-y divide-slate-100 rounded border border-slate-100">
          {items.map((item) => {
            const active = item.id === selectedId
            return (
              <li key={item.id} className={active ? 'bg-sky-50/40' : undefined}>
                <button
                  type="button"
                  onClick={() => onSelect(active ? null : item.id)}
                  aria-expanded={active}
                  className={`flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm hover:bg-slate-50 ${
                    active ? 'bg-sky-50' : ''
                  }`}
                >
                  <div className="min-w-0">
                    <div className="truncate font-mono text-xs text-slate-700">
                      {item.phone}
                    </div>
                    <div className="truncate text-xs text-slate-500">
                      {item.campaign_name} · {fmtTime(item.created_at)}
                    </div>
                  </div>
                  <span
                    className={`shrink-0 rounded px-2 py-0.5 font-mono text-[10px] uppercase ${
                      STATUS_BADGE[item.status] ?? 'bg-slate-100'
                    }`}
                  >
                    {STATUS_LABEL[item.status] ?? item.status}
                  </span>
                </button>
                {active ? (
                  <SlideDown>
                    <div className="space-y-4 border-t border-sky-100 bg-slate-50 px-3 py-3">
                      <h3 className="text-sm font-semibold text-slate-700">
                        Детали звонка
                      </h3>
                      {detailError ? (
                        <p className="text-sm text-red-700">{detailError}</p>
                      ) : null}
                      {detail && detail.id === item.id ? (
                        <CallDetailView detail={detail} />
                      ) : !detailError ? (
                        <p className="text-xs text-slate-500">Загрузка…</p>
                      ) : null}
                      {children?.({
                        attemptStatus: selectedAttemptStatus,
                        analysesKnown,
                        hasCompletedAnalysis,
                        completedDisplay,
                      })}
                    </div>
                  </SlideDown>
                ) : null}
              </li>
            )
          })}
        </ul>
      )}

      {nextCursor ? (
        <button
          type="button"
          onClick={() => void loadMore()}
          disabled={loadingMore}
          className="w-full rounded border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
        >
          {loadingMore ? 'Загрузка…' : 'Ещё'}
        </button>
      ) : null}
    </section>
  )
}

function CallDetailView({ detail }: { detail: CallAttemptDetail }) {
  return (
    <div className="space-y-4 text-sm">
      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <div>
          <dt className="text-slate-500">Статус</dt>
          <dd className="font-mono">
            {STATUS_LABEL[detail.status] ?? detail.status}
          </dd>
        </div>
        <div>
          <dt className="text-slate-500">Телефон</dt>
          <dd className="font-mono">{detail.contact.phone}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Часовой пояс</dt>
          <dd className="font-mono">{detail.contact.timezone}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Кампания</dt>
          <dd>{detail.campaign_name}</dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-slate-500">provider_call_id</dt>
          <dd className="truncate font-mono text-xs">
            {detail.provider_call_id ?? '—'}
          </dd>
        </div>
      </dl>

      <div>
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
          История статусов
        </h4>
        <ol className="space-y-2 border-l-2 border-slate-200 pl-3">
          {detail.status_history.map((h) => (
            <li key={historyKey(h)} className="relative">
              <span
                className={`inline-block rounded px-1.5 py-0.5 font-mono text-[10px] uppercase ${
                  STATUS_BADGE[h.status] ?? 'bg-slate-100'
                }`}
              >
                {STATUS_LABEL[h.status as CallAttemptStatus] ?? h.status}
              </span>
              <span className="ml-2 text-xs text-slate-500">
                {SOURCE_LABEL[h.source] ?? h.source}
                {h.event_type ? ` · ${h.event_type}` : ''}
                {h.sequence != null ? ` · seq ${h.sequence}` : ''}
              </span>
              <div className="text-xs text-slate-400">{fmtTime(h.at)}</div>
            </li>
          ))}
        </ol>
      </div>

      <div>
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
          LLM-разбор
        </h4>
        {detail.analyses.length === 0 ? (
          <p className="text-xs text-slate-500">Разборов пока нет</p>
        ) : (
          <ul className="space-y-2">
            {detail.analyses.map((a) => (
              <li
                key={a.id}
                className="rounded border border-slate-100 bg-slate-50 p-2"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span
                    className={`rounded px-1.5 py-0.5 font-mono text-[10px] uppercase ${
                      LLM_BADGE[a.status] ?? 'bg-slate-100'
                    }`}
                  >
                    {LLM_LABEL[a.status] ?? a.status}
                  </span>
                  <span className="truncate font-mono text-[10px] text-slate-500">
                    {a.id}
                  </span>
                </div>
                {a.error ? (
                  <p className="mt-1 text-xs text-red-700">
                    {formatAnalysisError(a.error)}
                  </p>
                ) : null}
                {a.result?.summary || a.partial?.summary ? (
                  <p className="mt-1 text-xs text-slate-700">
                    {a.result?.summary ?? a.partial?.summary}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
          Доставка в CRM
        </h4>
        {detail.crm ? (
          <div className="rounded border border-slate-100 bg-slate-50 p-2">
            <span
              className={`rounded px-1.5 py-0.5 font-mono text-[10px] uppercase ${
                CRM_BADGE[detail.crm.state] ?? 'bg-slate-100'
              }`}
            >
              {CRM_LABEL[detail.crm.state] ?? detail.crm.state}
            </span>
            <dl className="mt-2 grid grid-cols-2 gap-1 text-xs">
              <div>
                <dt className="text-slate-500">попытки</dt>
                <dd className="font-mono">{detail.crm.attempts}</dd>
              </div>
              <div>
                <dt className="text-slate-500">доставлено</dt>
                <dd className="font-mono">{fmtTime(detail.crm.delivered_at)}</dd>
              </div>
              <div className="col-span-2">
                <dt className="text-slate-500">последняя ошибка</dt>
                <dd className="font-mono text-red-700">
                  {formatCrmLastError(detail.crm.last_error)}
                </dd>
              </div>
            </dl>
          </div>
        ) : (
          <p className="text-xs text-slate-500">
            Нет записи в CRM outbox (попытка ещё не терминальна или не
            засеяна)
          </p>
        )}
      </div>

      {detail.transcript ? (
        <div>
          <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Транскрипт
          </h4>
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-2 text-xs text-slate-700">
            {detail.transcript}
          </pre>
        </div>
      ) : null}
    </div>
  )
}
