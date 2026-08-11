import type { CallAttemptStatus } from './api'

export const STATUSES: CallAttemptStatus[] = [
  'queued',
  'dialing',
  'in_progress',
  'completed',
  'failed',
  'no_answer',
]

export const STATUS_LABEL: Record<CallAttemptStatus, string> = {
  queued: 'в очереди',
  dialing: 'набор',
  in_progress: 'идёт',
  completed: 'завершён',
  failed: 'ошибка',
  no_answer: 'не ответил',
}

export const STATUS_BADGE: Record<string, string> = {
  queued: 'bg-slate-200 text-slate-800',
  dialing: 'bg-amber-200 text-amber-900',
  in_progress: 'bg-sky-200 text-sky-900',
  completed: 'bg-emerald-200 text-emerald-900',
  failed: 'bg-red-200 text-red-900',
  no_answer: 'bg-zinc-300 text-zinc-800',
}

export const CRM_LABEL: Record<string, string> = {
  pending: 'ожидает',
  retrying: 'повтор',
  delivered: 'доставлено',
}

export const CRM_BADGE: Record<string, string> = {
  pending: 'bg-amber-100 text-amber-900',
  retrying: 'bg-orange-100 text-orange-900',
  delivered: 'bg-emerald-100 text-emerald-900',
}

export const LLM_LABEL: Record<string, string> = {
  queued: 'в очереди',
  streaming: 'стрим',
  done: 'готово',
  error: 'ошибка',
  cancelled: 'отменён',
}

export const LLM_BADGE: Record<string, string> = {
  queued: 'bg-amber-100 text-amber-900',
  streaming: 'bg-sky-100 text-sky-900',
  done: 'bg-emerald-100 text-emerald-900',
  error: 'bg-red-100 text-red-900',
  cancelled: 'bg-zinc-200 text-zinc-800',
}

export const FEED_STATUS_LABEL: Record<'idle' | 'open' | 'reconnecting', string> =
  {
    idle: 'ожидание',
    open: 'открыта',
    reconnecting: 'переподключение',
  }

export const SOURCE_LABEL: Record<string, string> = {
  claim: 'выдача',
  webhook: 'вебхук',
  abort: 'abort',
}

export function fmtTime(value: string | null | undefined): string {
  if (!value) return '—'
  try {
    return new Date(value).toLocaleString('ru-RU')
  } catch {
    return value
  }
}

export function sanitizePhonePrefix(raw: string): string | undefined {
  const trimmed = raw.trim()
  if (!trimmed) return undefined
  if (!/^\+?[0-9]{1,15}$/.test(trimmed)) return undefined
  return trimmed
}
