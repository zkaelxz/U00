import { Field } from '../../../../components/Field'
import { humanize } from '../../../../components/labels'
import type { TranslateEngine } from '../../../../types/translate'
import { modelOptionLabel } from '../../../../api/translate'

// Engine and model pickers shared by the check jobs and fix-flagged. Paid
// engines stay behind the server's engines.paid check; this only chooses.
export function EngineModelFields({
  engines,
  defaultEngine,
  engine,
  model,
  help,
  onChange,
}: {
  engines: TranslateEngine[]
  defaultEngine: string
  engine: string
  model: string
  help: string
  onChange: (next: { engine: string; model: string }) => void
}) {
  const models = engines.find((e) => e.name === (engine || defaultEngine))?.models ?? null
  const engineLabel = (name: string) => {
    const e = engines.find((x) => x.name === name)
    return e ? `${e.label}${e.key_configured ? '' : ' (no key)'}` : humanize('engine', name)
  }
  return (
    <>
      <Field label="Engine" help={help}>
        <select value={engine} onChange={(e) => onChange({ engine: e.target.value, model: '' })}>
          <option value="">Default{defaultEngine ? ` (${humanize('engine', defaultEngine)})` : ''}</option>
          {engines.map((e) => (
            <option key={e.name} value={e.name}>
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
              <option key={m} value={m}>
                {modelOptionLabel(engines.find((e) => e.name === (engine || defaultEngine)), m)}
              </option>
            ))}
          </select>
        </Field>
      ) : (
        <Field label="Model" help="Blank uses the engine's default model.">
          <input value={model} maxLength={200} onChange={(e) => onChange({ engine, model: e.target.value })} />
        </Field>
      )}
    </>
  )
}
