import type { CallAttemptDetail, CallAttemptStatus, StatusHistoryItem } from '../lib/api'
import { formatAnalysisError, formatCrmLastError } from '../lib/formatError'
import {
  CRM_BADGE,
  CRM_LABEL,
  LLM_BADGE,
  LLM_LABEL,
  SOURCE_LABEL,
  STATUS_BADGE,
  STATUS_LABEL,
  fmtTime,
} from '../lib/callsPanelShared'

function historyKey(h: StatusHistoryItem): string {
  return [
    h.source,
    h.status,
    h.sequence == null ? 'noseq' : String(h.sequence),
    h.event_type ?? 'notype',
    h.at,
  ].join(':')
}

export function CallDetailView({ detail }: { detail: CallAttemptDetail }) {
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
