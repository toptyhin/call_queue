import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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
} from './api'
import { sanitizePhonePrefix } from './callsPanelShared'

const PAGE_SIZE = 20

type ListFilters = {
  status: CallAttemptStatus | ''
  phone: string
  createdFrom: string
  createdTo: string
}

/** Convert datetime-local value to ISO for the API; empty → undefined. */
function localToIso(value: string): string | undefined {
  if (!value) return undefined
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return undefined
  return d.toISOString()
}

function matchesFilters(
  item: CallAttemptListItem,
  filters: ListFilters,
): boolean {
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

export type CallsPanelChildProps = {
  attemptStatus: CallAttemptStatus | null
  /** Detail for the selection is loaded (analyses list is trustworthy). */
  analysesKnown: boolean
  /** Selected attempt already has at least one analysis in `done`. */
  hasCompletedAnalysis: boolean
  /** Latest `done` analysis payload for the result panel (if any). */
  completedDisplay: AnalysisResult | PartialAnalysisResult | null
}

export type CallsPanelModel = {
  items: CallAttemptListItem[]
  nextCursor: string | null
  detail: CallAttemptDetail | null
  listError: string | null
  detailError: string | null
  listLoading: boolean
  loadingMore: boolean
  feedStatus: 'idle' | 'open' | 'reconnecting'
  status: CallAttemptStatus | ''
  setStatus: (value: CallAttemptStatus | '') => void
  phoneInput: string
  setPhoneInput: (value: string) => void
  phoneInvalid: boolean
  createdFrom: string
  setCreatedFrom: (value: string) => void
  createdTo: string
  setCreatedTo: (value: string) => void
  hasActiveFilters: boolean
  clearFilters: () => void
  loadMore: () => Promise<void>
  childProps: CallsPanelChildProps
}

export function useCallsPanel(selectedId: string | null): CallsPanelModel {
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
      setListLoading(false)
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
      setLoadingMore(false)
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

  return {
    items,
    nextCursor,
    detail,
    listError,
    detailError,
    listLoading,
    loadingMore,
    feedStatus,
    status,
    setStatus,
    phoneInput,
    setPhoneInput,
    phoneInvalid,
    createdFrom,
    setCreatedFrom,
    createdTo,
    setCreatedTo,
    hasActiveFilters,
    clearFilters,
    loadMore,
    childProps: {
      attemptStatus: selectedAttemptStatus,
      analysesKnown,
      hasCompletedAnalysis,
      completedDisplay,
    },
  }
}
