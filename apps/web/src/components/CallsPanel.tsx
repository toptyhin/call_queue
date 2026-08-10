import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchEventSource } from '@microsoft/fetch-event-source'
import {
  getCallAttempt,
  listCallAttempts,
  type CallAttemptDetail,
  type CallAttemptListItem,
  type CallFeedAnalysisEvent,
  type CallFeedCrmEvent,
  type CrmDelivery,
  type StatusHistoryItem,
} from '../lib/api'

const STATUS_BADGE: Record<string, string> = {
  queued: 'bg-slate-200 text-slate-800',
  dialing: 'bg-amber-200 text-amber-900',
  in_progress: 'bg-sky-200 text-sky-900',
  completed: 'bg-emerald-200 text-emerald-900',
  failed: 'bg-red-200 text-red-900',
  no_answer: 'bg-zinc-300 text-zinc-800',
}

const CRM_BADGE: Record<string, string> = {
  pending: 'bg-amber-100 text-amber-900',
  retrying: 'bg-orange-100 text-orange-900',
  delivered: 'bg-emerald-100 text-emerald-900',
}

const LLM_BADGE: Record<string, string> = {
  queued: 'bg-amber-100 text-amber-900',
  streaming: 'bg-sky-100 text-sky-900',
  done: 'bg-emerald-100 text-emerald-900',
  error: 'bg-red-100 text-red-900',
  cancelled: 'bg-zinc-200 text-zinc-800',
}

function fmtTime(value: string | null | undefined): string {
  if (!value) return '—'
  try {
    return new Date(value).toLocaleString()
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

function upsertAttempt(
  prev: CallAttemptListItem[],
  item: CallAttemptListItem,
): CallAttemptListItem[] {
  const idx = prev.findIndex((x) => x.id === item.id)
  if (idx === -1) return [item, ...prev]
  return prev.map((x, i) => (i === idx ? item : x))
}

type Props = {
  selectedId: string | null
  onSelect: (attemptId: string) => void
}

export function CallsPanel({ selectedId, onSelect }: Props) {
  const [items, setItems] = useState<CallAttemptListItem[]>([])
  const [detail, setDetail] = useState<CallAttemptDetail | null>(null)
  const [listError, setListError] = useState<string | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [feedStatus, setFeedStatus] = useState<'idle' | 'open' | 'reconnecting'>(
    'idle',
  )
  const selectedRef = useRef(selectedId)
  const crmOverrideRef = useRef<Record<string, CrmDelivery | null>>({})
  const detailReqRef = useRef(0)
  const listReqRef = useRef(0)

  useEffect(() => {
    selectedRef.current = selectedId
  }, [selectedId])

  const refreshList = useCallback(async () => {
    const reqId = ++listReqRef.current
    setListError(null)
    try {
      const page = await listCallAttempts({ limit: 50 })
      if (reqId !== listReqRef.current) return
      setItems(page.items)
    } catch (err) {
      if (reqId !== listReqRef.current) return
      setListError(err instanceof Error ? err.message : String(err))
    }
  }, [])

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
          throw new Error(`calls SSE open failed: HTTP ${response.status}`)
        }
        setFeedStatus('open')
      },
      onmessage(ev) {
        if (ev.event === 'attempt') {
          try {
            const item = JSON.parse(ev.data) as CallAttemptListItem
            setItems((prev) => upsertAttempt(prev, item))
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

  return (
    <section className="space-y-3 rounded border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Calls
        </h2>
        <span className="font-mono text-xs text-slate-500">
          feed: {feedStatus}
        </span>
      </div>

      {listError ? (
        <p className="text-sm text-red-700">{listError}</p>
      ) : null}

      {items.length === 0 && !listError ? (
        <p className="text-sm text-slate-500">
          No call attempts yet. Run <code className="font-mono">make seed</code>{' '}
          or claim a contact.
        </p>
      ) : (
        <ul className="divide-y divide-slate-100 rounded border border-slate-100">
          {items.map((item) => {
            const active = item.id === selectedId
            return (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => onSelect(item.id)}
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
                    {item.status}
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
      )}

      {selectedId ? (
        <div className="space-y-4 border-t border-slate-100 pt-4">
          <h3 className="text-sm font-semibold text-slate-700">
            Call detail
          </h3>
          {detailError ? (
            <p className="text-sm text-red-700">{detailError}</p>
          ) : null}
          {detail ? <CallDetailView detail={detail} /> : null}
        </div>
      ) : null}
    </section>
  )
}

function CallDetailView({ detail }: { detail: CallAttemptDetail }) {
  return (
    <div className="space-y-4 text-sm">
      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <div>
          <dt className="text-slate-500">Status</dt>
          <dd className="font-mono">{detail.status}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Phone</dt>
          <dd className="font-mono">{detail.contact.phone}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Timezone</dt>
          <dd className="font-mono">{detail.contact.timezone}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Campaign</dt>
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
          Call status timeline
        </h4>
        <ol className="space-y-2 border-l-2 border-slate-200 pl-3">
          {detail.status_history.map((h) => (
            <li key={historyKey(h)} className="relative">
              <span
                className={`inline-block rounded px-1.5 py-0.5 font-mono text-[10px] uppercase ${
                  STATUS_BADGE[h.status] ?? 'bg-slate-100'
                }`}
              >
                {h.status}
              </span>
              <span className="ml-2 text-xs text-slate-500">
                {h.source}
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
          LLM analysis
        </h4>
        {detail.analyses.length === 0 ? (
          <p className="text-xs text-slate-500">No analyses yet</p>
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
                    {a.status}
                  </span>
                  <span className="truncate font-mono text-[10px] text-slate-500">
                    {a.id}
                  </span>
                </div>
                {a.error ? (
                  <p className="mt-1 text-xs text-red-700">{a.error}</p>
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
          CRM delivery
        </h4>
        {detail.crm ? (
          <div className="rounded border border-slate-100 bg-slate-50 p-2">
            <span
              className={`rounded px-1.5 py-0.5 font-mono text-[10px] uppercase ${
                CRM_BADGE[detail.crm.state] ?? 'bg-slate-100'
              }`}
            >
              {detail.crm.state}
            </span>
            <dl className="mt-2 grid grid-cols-2 gap-1 text-xs">
              <div>
                <dt className="text-slate-500">attempts</dt>
                <dd className="font-mono">{detail.crm.attempts}</dd>
              </div>
              <div>
                <dt className="text-slate-500">delivered_at</dt>
                <dd className="font-mono">{fmtTime(detail.crm.delivered_at)}</dd>
              </div>
              <div className="col-span-2">
                <dt className="text-slate-500">last_error</dt>
                <dd className="font-mono text-red-700">
                  {detail.crm.last_error ?? '—'}
                </dd>
              </div>
            </dl>
          </div>
        ) : (
          <p className="text-xs text-slate-500">
            No CRM outbox row (attempt not terminal yet, or not seeded)
          </p>
        )}
      </div>

      {detail.transcript ? (
        <div>
          <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Transcript
          </h4>
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-2 text-xs text-slate-700">
            {detail.transcript}
          </pre>
        </div>
      ) : null}
    </div>
  )
}
