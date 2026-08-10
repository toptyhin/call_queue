/** Map stable server-side analysis error codes to short RU labels. */
const ANALYSIS_ERROR_RU: Record<string, string> = {
  'invalid provider result':
    'Провайдер вернул некорректный результат разбора',
  'provider stream broken, retries exhausted':
    'Стрим провайдера оборвался; частичный результат сохранён',
  'provider stream prefix mismatch on resume':
    'Несовпадение префикса стрима при переподключении',
  'internal consumer error': 'Внутренняя ошибка разбора',
  interrupted_restart: 'Разбор прерван перезапуском сервиса',
}

/** CRM outbox `last_error` values written by the poller. */
const CRM_ERROR_RU: Record<string, string> = {
  'crm request failed': 'CRM недоступна',
}

/**
 * Format `analyses.error` for UI. Unknown codes pass through unchanged
 * (legacy rows may still contain longer diagnostic text).
 */
export function formatAnalysisError(message: string | null | undefined): string | null {
  if (!message) return null
  // Strip accidental legacy suffix: "invalid provider result: <pydantic dump>"
  const base = message.startsWith('invalid provider result')
    ? 'invalid provider result'
    : message
  return ANALYSIS_ERROR_RU[base] ?? message
}

/** Format CRM `last_error` for UI (HTTP NNN or stable codes). */
export function formatCrmLastError(message: string | null | undefined): string {
  if (!message) return '—'
  if (CRM_ERROR_RU[message]) return CRM_ERROR_RU[message]
  const http = /^HTTP (\d{3})$/.exec(message)
  if (http) return `ошибка CRM (HTTP ${http[1]})`
  return message
}
