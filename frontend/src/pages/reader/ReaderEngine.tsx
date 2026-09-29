import { engineShortName } from '../../api/translate'
import { Field } from '../../components/Field'
import { routeHref } from '../../router'
import type { TranslateEngine } from '../../types/translate'

// The AI engine picker shared by Words and Story tools. The server treats an
// omitted engine as Claude (paid), so a request always names the chosen one.
export function EnginePicker({ engines, engine, onChange }: {
  engines: TranslateEngine[]
  engine: string
  onChange: (name: string) => void
}) {
  if (engines.length === 0) {
    return (
      <p className="muted">
        No AI engine is set up. Add a key in <a href={routeHref({ name: 'settings' })}>Settings</a> or run Ollama.
      </p>
    )
  }
  return (
    <Field label="AI engine" help="Free engines (like Ollama) run locally. Paid engines need an account that may use them.">
      <select value={engine} onChange={(e) => onChange(e.target.value)}>
        {engines.map((e) => (
          <option key={e.name} value={e.name}>
            {engineShortName(e)} {e.free ? '(free)' : '(paid)'}
          </option>
        ))}
      </select>
    </Field>
  )
}
