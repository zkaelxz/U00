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

/** "Run settings" for one job's Details panel; renders nothing without any. */
export function RunSettings({ result }: { result: Record<string, unknown> | null | undefined }) {
  const rows = runSettingRows(result)
  if (rows.length === 0) return null
  const engine = engineThatRan(result)
  return (
    <section aria-label="Run settings">
      <h4 className="stage-times-title">Run settings</h4>
      {engine && <p data-testid="engine-that-ran">Engine that ran: {engine}</p>}
      <dl>
        {rows.map(([label, value]) => (
          <span key={label} className="jobs-field"><dt>{label}</dt><dd>{value}</dd></span>
        ))}
      </dl>
    </section>
  )
}
