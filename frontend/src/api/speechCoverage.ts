import type { SpeechCoverageStatus } from '../types/speechCoverage'
import { getJson, postJson } from './client'

type Fetch = typeof fetch

export const getSpeechCoverage = (id: number, f?: Fetch) =>
  getJson<SpeechCoverageStatus>(`/api/transcribe/dramas/${id}/speech-coverage`, f)

export const startSpeechCoverage = (id: number, minGapSeconds: number, f?: Fetch) =>
  postJson<{ job_id: string }>(`/api/transcribe/dramas/${id}/speech-coverage`, { min_gap_seconds: minGapSeconds }, f)
