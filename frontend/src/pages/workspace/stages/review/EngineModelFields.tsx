import { Field } from '../../../../components/Field'
import { humanize } from '../../../../components/labels'
import type { TranslateEngine } from '../../../../types/translate'
import { engineNotesHelp, engineOptionLabel, engineSummary, modelOptionLabel } from '../../../../api/translate'
import { AI_ENGINE_LABEL } from '../../../../helpText'

// Engine and model pickers shared by the check jobs and fix-flagged. Paid
// engines stay behind the server's engines.paid check; this only chooses.
export function EngineModelFields({
  engines,
  defaultEngine,
  engine,
  model,
  help,
  onChange,
  markPaid,
  blocked,
  modelBlocked,
}: {
  engines: TranslateEngine[]
  defaultEngine: string
  engine: string
  model: string
  // Empty: no (i) button, for a panel that shows the chosen engine's note itself.
  help: string
  onChange: (next: { engine: string; model: string }) => void
  // Per-line AI picker: paid engines say so and show the engine's note (price hint) under
  // the selects, and an engine or model that can't be used is disabled with its reason.
  markPaid?: boolean
  blocked?: (engine: TranslateEngine) => string | null
  modelBlocked?: (engine: TranslateEngine, model: string) => string | null
}) {
  const chosen = engines.find((e) => e.name === (engine || defaultEngine))
  const models = chosen?.models ?? null
  const modelReason = (m: string) => (chosen ? modelBlocked?.(chosen, m) ?? null : null)
  const engineLabel = (name: string) => {
    const e = engines.find((x) => x.name === name)
    if (!e) return humanize('engine', name)
    const reason = blocked?.(e)
    return `${engineOptionLabel(e)}${markPaid && !e.free ? ' · paid' : ''}${reason ? ` (${reason})` : ''}`
  }
  return (
    <>
      <Field label={AI_ENGINE_LABEL} help={help ? `${help} ${engineNotesHelp(engines)}` : undefined}>
        <select value={engine} onChange={(e) => onChange({ engine: e.target.value, model: '' })}>
          <option value="">Default{defaultEngine ? ` (${humanize('engine', defaultEngine)})` : ''}</option>
          {engines.map((e) => (
            <option key={e.name} value={e.name} disabled={!!blocked?.(e)}>
              {engineLabel(e.name)}
            </option>
          ))}
        </select>
      </Field>
      {models && models.length > 0 ? (
        <Field label="Model">
          <select value={model} onChange={(e) => onChange({ engine, model: e.target.value })}>
            <option value="">Engine default</option>
            {models.map((m) => (
              <option key={m} value={m} disabled={!!modelReason(m)}>
                {modelOptionLabel(chosen, m)}
                {modelReason(m) ? ` (${modelReason(m)})` : ''}
              </option>
            ))}
          </select>
        </Field>
      ) : (
        <Field label="Model" help="Blank uses the engine's default model.">
          <input value={model} maxLength={200} onChange={(e) => onChange({ engine, model: e.target.value })} />
        </Field>
      )}
      {markPaid && chosen && (
        <p className="muted review-ai-engine-hint" data-testid="line-ai-engine-hint">
          {engineSummary(chosen.label)}
        </p>
      )}
    </>
  )
}
