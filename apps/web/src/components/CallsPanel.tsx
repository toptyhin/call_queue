import type { ReactNode } from 'react'
import {
  useCallsPanel,
  type CallsPanelChildProps,
} from '../lib/useCallsPanel'
import { CallsAttemptList } from './CallsAttemptList'
import { CallsPanelFilters } from './CallsPanelFilters'
import { FEED_STATUS_LABEL } from '../lib/callsPanelShared'

type Props = {
  selectedId: string | null
  onSelect: (attemptId: string | null) => void
  /** Rendered under the selected row (call detail is always shown above). */
  children?: (ctx: CallsPanelChildProps) => ReactNode
}

export type { CallsPanelChildProps }

export function CallsPanel({ selectedId, onSelect, children }: Props) {
  const panel = useCallsPanel(selectedId)

  return (
    <section className="space-y-3 rounded border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Звонки
        </h2>
        <span className="font-mono text-xs text-slate-500">
          лента: {FEED_STATUS_LABEL[panel.feedStatus]}
        </span>
      </div>

      <CallsPanelFilters
        status={panel.status}
        onStatusChange={panel.setStatus}
        phoneInput={panel.phoneInput}
        onPhoneInputChange={panel.setPhoneInput}
        phoneInvalid={panel.phoneInvalid}
        createdFrom={panel.createdFrom}
        onCreatedFromChange={panel.setCreatedFrom}
        createdTo={panel.createdTo}
        onCreatedToChange={panel.setCreatedTo}
        hasActiveFilters={panel.hasActiveFilters}
        onClearFilters={panel.clearFilters}
      />

      {panel.listError ? (
        <p className="text-sm text-red-700">{panel.listError}</p>
      ) : null}

      {panel.items.length === 0 && !panel.listError && !panel.listLoading ? (
        <p className="text-sm text-slate-500">
          {panel.hasActiveFilters ? (
            <>Нет попыток звонка по фильтрам.</>
          ) : (
            <>
              Попыток звонка пока нет. Выполните{' '}
              <code className="font-mono">make seed</code> или claim контакта.
            </>
          )}
        </p>
      ) : (
        <CallsAttemptList
          items={panel.items}
          selectedId={selectedId}
          onSelect={onSelect}
          detail={panel.detail}
          detailError={panel.detailError}
          childProps={panel.childProps}
        >
          {children}
        </CallsAttemptList>
      )}

      {panel.nextCursor ? (
        <button
          type="button"
          onClick={() => void panel.loadMore()}
          disabled={panel.loadingMore}
          className="w-full rounded border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
        >
          {panel.loadingMore ? 'Загрузка…' : 'Ещё'}
        </button>
      ) : null}
    </section>
  )
}
