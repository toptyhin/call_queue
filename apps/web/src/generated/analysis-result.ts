/**
 * GENERATED — do not edit by hand.
 *
 * Temporary hand-written placeholder compatible with
 * packages/shared/analysis-result.schema.json until `make codegen` overwrites this file.
 */
import { z } from 'zod'

export const AnalysisResultSchema = z.object({
  summary: z.string(),
  objections: z.array(z.string()),
  next_step: z.enum([
    'call_back',
    'send_proposal',
    'disqualify',
    'escalate',
  ]),
  lead_score: z.number().int().min(0).max(100),
  confidence: z.number().min(0).max(1),
})

export type AnalysisResult = z.infer<typeof AnalysisResultSchema>

const PartialAnalysisResultSchema = AnalysisResultSchema.partial()

export type PartialAnalysisResult = z.infer<typeof PartialAnalysisResultSchema>
