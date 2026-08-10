import type { AnalysisResult as AnalysisResultModel, PartialAnalysisResult } from '../generated/analysis-result'

type Props = {
  display: AnalysisResultModel | PartialAnalysisResult | null
  /** Flatter layout when nested under a call row. */
  embedded?: boolean
}

function Field({
  label,
  value,
  mono,
  embedded,
}: {
  label: string
  value: string | undefined
  mono?: boolean
  embedded?: boolean
}) {
  return (
    <div>
      <div className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
      </div>
      <pre
        className={`min-h-[2rem] whitespace-pre-wrap rounded p-2 text-sm ${
          embedded ? 'bg-white' : 'bg-slate-50'
        } ${mono ? 'font-mono' : ''} ${
          value === undefined ? 'text-slate-400' : 'text-slate-800'
        }`}
      >
        {value === undefined ? '—' : value}
      </pre>
    </div>
  )
}

export function AnalysisResultView({ display, embedded = false }: Props) {
  return (
    <section
      className={
        embedded
          ? 'space-y-3 border-t border-slate-200 pt-3'
          : 'space-y-3 rounded border border-slate-200 bg-white p-4'
      }
    >
      <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
        Результат / частичный
      </h2>
      <Field embedded={embedded} label="сводка" value={display?.summary} />
      <Field
        embedded={embedded}
        label="возражения"
        value={
          display?.objections !== undefined
            ? JSON.stringify(display.objections, null, 2)
            : undefined
        }
        mono
      />
      <div className="grid grid-cols-3 gap-3">
        <Field
          embedded={embedded}
          label="следующий шаг"
          value={display?.next_step}
          mono
        />
        <Field
          embedded={embedded}
          label="оценка лида"
          value={
            display?.lead_score !== undefined
              ? String(display.lead_score)
              : undefined
          }
          mono
        />
        <Field
          embedded={embedded}
          label="уверенность"
          value={
            display?.confidence !== undefined
              ? String(display.confidence)
              : undefined
          }
          mono
        />
      </div>
    </section>
  )
}
