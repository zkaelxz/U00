/*
 * ModelSelect: the "Model" picker shared by the Translate stage and the Live
 * page. It renders nothing for an engine without a model list. The empty value
 * is "Engine default": the server then applies the engine's own default, so
 * changing that default needs no change here.
 */
import { modelOptionLabel } from '../api/translate'
import type { TranslateEngine } from '../types/translate'
import { Field } from './Field'

type Props = {
  engine: TranslateEngine | undefined
  value: string
  onChange: (model: string) => void
  disabled?: boolean
  help?: string
  // A model the caller starts on: it replaces "Engine default" and is marked "(default)".
  defaultModel?: string
}

export function ModelSelect({ engine, value, onChange, disabled, help, defaultModel }: Props) {
  const models = engine?.models ?? []
  if (models.length === 0) return null
  return (
    <Field label="Model" help={help}>
      <select value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
        {!defaultModel && <option value="">Engine default</option>}
        {models.map((m) => (
          <option key={m} value={m}>{modelOptionLabel(engine, m)}{m === defaultModel ? ' (default)' : ''}</option>
        ))}
      </select>
    </Field>
  )
}
