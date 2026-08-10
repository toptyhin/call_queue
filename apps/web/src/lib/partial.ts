import type { PartialAnalysisResult } from '../generated/analysis-result'

export type AnalysisField =
  | 'summary'
  | 'objections'
  | 'next_step'
  | 'lead_score'
  | 'confidence'

export type FieldBuffers = Record<AnalysisField, string>

export const ANALYSIS_FIELDS: AnalysisField[] = [
  'summary',
  'objections',
  'next_step',
  'lead_score',
  'confidence',
]

const NEXT_STEPS = new Set([
  'call_back',
  'send_proposal',
  'disqualify',
  'escalate',
])

export function emptyBuffers(): FieldBuffers {
  return {
    summary: '',
    objections: '',
    next_step: '',
    lead_score: '',
    confidence: '',
  }
}

export function isAnalysisField(value: string): value is AnalysisField {
  return (ANALYSIS_FIELDS as string[]).includes(value)
}

/** Concatenate deltas as text, then parse each field when valid. */
export function parseBuffers(buffers: FieldBuffers): PartialAnalysisResult {
  const partial: PartialAnalysisResult = {}

  if (buffers.summary.length > 0) {
    partial.summary = buffers.summary
  }

  if (buffers.objections.length > 0) {
    try {
      const parsed: unknown = JSON.parse(buffers.objections)
      if (
        Array.isArray(parsed) &&
        parsed.every((item) => typeof item === 'string')
      ) {
        partial.objections = parsed
      }
    } catch {
      /* incomplete JSON */
    }
  }

  if (NEXT_STEPS.has(buffers.next_step)) {
    partial.next_step = buffers.next_step as NonNullable<
      PartialAnalysisResult['next_step']
    >
  }

  if (buffers.lead_score.length > 0) {
    try {
      const parsed: unknown = JSON.parse(buffers.lead_score)
      if (
        typeof parsed === 'number' &&
        Number.isInteger(parsed) &&
        parsed >= 0 &&
        parsed <= 100
      ) {
        partial.lead_score = parsed
      }
    } catch {
      /* incomplete JSON */
    }
  }

  if (buffers.confidence.length > 0) {
    try {
      const parsed: unknown = JSON.parse(buffers.confidence)
      if (typeof parsed === 'number' && parsed >= 0 && parsed <= 1) {
        partial.confidence = parsed
      }
    } catch {
      /* incomplete JSON */
    }
  }

  return partial
}

export function isPartialEmpty(
  partial: PartialAnalysisResult | null | undefined,
): boolean {
  if (!partial) return true
  return (
    partial.summary === undefined &&
    partial.objections === undefined &&
    partial.next_step === undefined &&
    partial.lead_score === undefined &&
    partial.confidence === undefined
  )
}

/** Rebuild text buffers from a server-synced partial (best-effort). */
export function buffersFromPartial(
  partial: PartialAnalysisResult | null | undefined,
): FieldBuffers {
  const buffers = emptyBuffers()
  if (!partial) return buffers
  if (partial.summary !== undefined) buffers.summary = partial.summary
  if (partial.objections !== undefined) {
    buffers.objections = JSON.stringify(partial.objections)
  }
  if (partial.next_step !== undefined) buffers.next_step = partial.next_step
  if (partial.lead_score !== undefined) {
    buffers.lead_score = JSON.stringify(partial.lead_score)
  }
  if (partial.confidence !== undefined) {
    buffers.confidence = JSON.stringify(partial.confidence)
  }
  return buffers
}
