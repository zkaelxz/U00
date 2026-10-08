// Mirrors api/schemas/real_model_check.py.

export type RealModelCheckStatus = 'pass' | 'fail' | 'skipped'

export interface RealModelCheckResult {
  id: 'asr' | 'ocr' | 'translate'
  label: string
  status: RealModelCheckStatus
  reason: string
}

export interface RealModelCheckState {
  job_id: string
  job: { status: string | null; progress: number; message: string; error: string | null } | null
  checks: RealModelCheckResult[]
  finished: boolean
}
