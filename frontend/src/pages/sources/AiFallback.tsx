/*
 * AiFallback: the optional AI-assisted fallback for a pasted-URL import
 * (parity SO09). Off by default. On, it shows an engine picker that starts
 * on the saved default engine (Settings > Defaults). Engine names only: the
 * key stays on the PC, and a paid engine may need permission (403).
 *
 *   const [ai, setAi] = useState(AI_OFF)
 *   <AiFallback value={ai} onChange={setAi} engines={engines} />
 */
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import type { AiEngines } from '../../types/sourcesExtraction'
import { AI_HELP, type AiChoice, aiReason, effectiveEngine, engineLabel } from './extractionFormat'
import './extraction.css'

type Props = {
  value: AiChoice
  onChange: (next: AiChoice) => void
  engines: AiEngines | null
  error?: unknown
  disabled?: boolean
}

export function AiFallback({ value, onChange, engines, error, disabled }: Props) {
  const current = effectiveEngine(value, engines)
  const reason = aiReason(value, engines)
  return (
    <div className="sources-ai" role="group" aria-label="AI help">
      <div className="toggle-list">
        <Field label="Let AI help if the page is unclear" help={AI_HELP}>
          <Toggle checked={value.on} disabled={disabled} onChange={(on) => onChange({ ...value, on })} />
        </Field>
      </div>
      {value.on && engines && engines.engines.length > 0 && (
        <Field label="AI engine">
          <select
            value={current ?? ''}
            disabled={disabled}
            onChange={(e) => onChange({ ...value, engine: e.target.value || null })}
          >
            {!current && <option value="">Choose an engine…</option>}
            {engines.engines.map((n) => (
              <option key={n} value={n}>
                {engineLabel(n)}
                {n === engines.default ? ' (default)' : ''}
              </option>
            ))}
          </select>
        </Field>
      )}
      {reason && <p className="muted">{reason}</p>}
      {value.on && <ErrorBanner error={error} />}
    </div>
  )
}
