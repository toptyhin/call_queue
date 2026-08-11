import { useEffect, useState, type ReactNode } from 'react'
import type { CallAttemptDetail, CallAttemptListItem } from '../lib/api'
import type { CallsPanelChildProps } from '../lib/useCallsPanel'
import { CallDetailView } from './CallDetailView'
import { STATUS_BADGE, STATUS_LABEL, fmtTime } from '../lib/callsPanelShared'

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

type Props = {
  items: CallAttemptListItem[]
  selectedId: string | null
  onSelect: (attemptId: string | null) => void
  detail: CallAttemptDetail | null
  detailError: string | null
  childProps: CallsPanelChildProps
  children?: (ctx: CallsPanelChildProps) => ReactNode
}

export function CallsAttemptList({
  items,
  selectedId,
  onSelect,
  detail,
  detailError,
  childProps,
  children,
}: Props) {
  return (
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
                  {children?.(childProps)}
                </div>
              </SlideDown>
            ) : null}
          </li>
        )
      })}
    </ul>
  )
}
