import { useEffect, useState } from 'react'

import { withSignal } from '../../api/client'
import { getAsrAdvice } from '../../api/workspace'
import type { AsrAdviceNote, AsrAdviceRequest } from '../../types/workspace'
import { backendRunsWhisper } from '../workspace/stages/transcribeRun'

type Scalar = string | number | boolean

/** The server's allow-listed run_settings as label/value rows, or [] when the
 * job has none (older jobs) or the value isn't a flat object. */
export function runSettingRows(result: Record<string, unknown> | null | undefined): Array<[string, string]> {
  const raw = result?.run_settings
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return []
  return Object.entries(raw as Record<string, unknown>)
    .filter((entry): entry is [string, Scalar] => ['string', 'number', 'boolean'].includes(typeof entry[1]))
    .map(([key, value]) => [
      key.replace(/_/g, ' '),
      typeof value === 'boolean' ? (value ? 'on' : 'off') : String(value),
    ])
}

/** "Engine that ran: Qwen3-ASR (Whisper model setting not used)" for a run whose backend skipped Whisper, else null.
 *  The whisper_size row is still recorded, so without this line it reads as if that model ran. */
export function engineThatRan(result: Record<string, unknown> | null | undefined): string | null {
  const raw = result?.run_settings
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const { asr_backend: backend, transcript_mode: mode } = raw as Record<string, unknown>
  // A supplied transcript is aligned, not transcribed, so the stored backend did not run.
  if (typeof backend !== 'string' || backendRunsWhisper(backend) || (mode !== undefined && mode !== 'whisper')) return null
  const name = backend === 'anime_whisper' ? 'Anime-whisper' : 'Qwen3-ASR'
  return `${name} (Whisper model setting not used)`
}

/** The advisor's question for a finished transcription run, or null for any other job. */
export function adviceRequestFromRun(result: Record<string, unknown> | null | undefined): AsrAdviceRequest | null {
  const raw = result?.run_settings
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const r = raw as Record<string, unknown>
  if (typeof r.asr_backend !== 'string') return null
  const flag = (k: string) => r[k] === true
  return {
    language: typeof r.source_language === 'string' ? r.source_language : '',
    media_type: '',
    asr_backend: r.asr_backend,
    whisper_size: typeof r.whisper_size === 'string' ? r.whisper_size : '',
    separate_vocals_first: flag('separate_vocals_first'),
    whisper_fast_mode: flag('whisper_fast_mode'),
    use_groq: flag('use_groq'),
    has_transcript: r.transcript_mode === 'have_transcript',
    alignment_method: typeof r.alignment_method === 'string' ? r.alignment_method : '',
    use_gpu: typeof r.use_gpu === 'boolean' ? r.use_gpu : null,
    check_environment: false,
  }
}

/** The warnings that applied to the combination a finished run used; nothing when none did. */
function RunAdvice({ request }: { request: AsrAdviceRequest }) {
  const key = JSON.stringify(request)
  const [notes, setNotes] = useState<AsrAdviceNote[]>([])
  useEffect(() => {
    const ctl = new AbortController()
    getAsrAdvice(JSON.parse(key) as AsrAdviceRequest, withSignal(ctl.signal))
      .then((all) => setNotes(all.filter((n) => n.severity !== 'tip')))
      .catch(() => setNotes([]))
    return () => ctl.abort()
  }, [key])
  if (notes.length === 0) return null
  return (
    <ul className="muted" aria-label="Notes on these settings" data-testid="run-advice">
      {notes.map((n) => <li key={n.id}>{n.text}</li>)}
    </ul>
  )
}

/** "Run settings" for one job's Details panel; renders nothing without any. */
export function RunSettings({ result }: { result: Record<string, unknown> | null | undefined }) {
  const rows = runSettingRows(result)
  if (rows.length === 0) return null
  const engine = engineThatRan(result)
  const request = adviceRequestFromRun(result)
  return (
    <section aria-label="Run settings">
      <h4 className="stage-times-title">Run settings</h4>
      {engine && <p data-testid="engine-that-ran">Engine that ran: {engine}</p>}
      {request && <RunAdvice request={request} />}
      <dl>
        {rows.map(([label, value]) => (
          <span key={label} className="jobs-field"><dt>{label}</dt><dd>{value}</dd></span>
        ))}
      </dl>
    </section>
  )
}
