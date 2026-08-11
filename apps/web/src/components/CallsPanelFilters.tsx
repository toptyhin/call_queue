import type { CallAttemptStatus } from '../lib/api'
import { STATUSES, STATUS_LABEL } from '../lib/callsPanelShared'

type Props = {
  status: CallAttemptStatus | ''
  onStatusChange: (value: CallAttemptStatus | '') => void
  phoneInput: string
  onPhoneInputChange: (value: string) => void
  phoneInvalid: boolean
  createdFrom: string
  onCreatedFromChange: (value: string) => void
  createdTo: string
  onCreatedToChange: (value: string) => void
  hasActiveFilters: boolean
  onClearFilters: () => void
}

export function CallsPanelFilters({
  status,
  onStatusChange,
  phoneInput,
  onPhoneInputChange,
  phoneInvalid,
  createdFrom,
  onCreatedFromChange,
  createdTo,
  onCreatedToChange,
  hasActiveFilters,
  onClearFilters,
}: Props) {
  return (
    <div className="grid gap-2 rounded border border-slate-100 bg-slate-50 p-3 sm:grid-cols-2 lg:grid-cols-4">
      <label className="block text-xs text-slate-600">
        Статус
        <select
          value={status}
          onChange={(e) =>
            onStatusChange(e.target.value as CallAttemptStatus | '')
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
          onChange={(e) => onPhoneInputChange(e.target.value)}
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
          onChange={(e) => onCreatedFromChange(e.target.value)}
          className="mt-1 w-full rounded border border-slate-200 bg-white px-2 py-1.5 font-mono text-xs"
        />
      </label>
      <label className="block text-xs text-slate-600">
        Создан по
        <input
          type="datetime-local"
          value={createdTo}
          onChange={(e) => onCreatedToChange(e.target.value)}
          className="mt-1 w-full rounded border border-slate-200 bg-white px-2 py-1.5 font-mono text-xs"
        />
      </label>
      {hasActiveFilters ? (
        <div className="sm:col-span-2 lg:col-span-4">
          <button
            type="button"
            onClick={onClearFilters}
            className="rounded border border-slate-200 bg-white px-2 py-1 text-xs text-slate-600 hover:bg-slate-100"
          >
            Сбросить фильтры
          </button>
        </div>
      ) : null}
    </div>
  )
}
