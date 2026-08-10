import type { AnalysisResult as AnalysisResultModel, PartialAnalysisResult } from '../generated/analysis-result'

type Props = {
  display: AnalysisResultModel | PartialAnalysisResult | null
}

function Field({
  label,
  value,
  mono,
}: {
  label: string
  value: string | undefined
  mono?: boolean
}) {
  return (
    <div>
      <div className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
      </div>
      <pre
        className={`min-h-[2rem] whitespace-pre-wrap rounded bg-slate-50 p-2 text-sm ${
          mono ? 'font-mono' : ''
        } ${value === undefined ? 'text-slate-400' : 'text-slate-800'}`}
      >
        {value === undefined ? '—' : value}
      </pre>
    </div>
  )
}

export function AnalysisResultView({ display }: Props) {
  return (
    <section className="space-y-3 rounded border border-slate-200 bg-white p-4">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
        Result / partial
      </h2>
      <Field label="summary" value={display?.summary} />
      <Field
        label="objections"
        value={
          display?.objections !== undefined
            ? JSON.stringify(display.objections, null, 2)
            : undefined
        }
        mono
      />
      <Field label="next_step" value={display?.next_step} mono />
      <Field
        label="lead_score"
        value={
          display?.lead_score !== undefined
            ? String(display.lead_score)
            : undefined
        }
        mono
      />
      <Field
        label="confidence"
        value={
          display?.confidence !== undefined
            ? String(display.confidence)
            : undefined
        }
        mono
      />
    </section>
  )
}
