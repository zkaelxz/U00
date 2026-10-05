import { engineShortName } from '../../api/translate'
import { Field } from '../../components/Field'
import { routeHref } from '../../router'
import type { TranslateEngine } from '../../types/translate'
import { AI_ENGINE_LABEL } from '../../helpText'

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
    <Field label={AI_ENGINE_LABEL} help="Free engines (like Ollama) run locally. Paid engines need an account that may use them.">
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

// Where Story tools renders its picker, so other Sections can jump to it.
export const ENGINE_PICKER_ID = 'reader-engine-picker'

// Open the Story tools Section (a <details>) and focus its engine picker.
function focusPicker() {
  const box = document.getElementById(ENGINE_PICKER_ID)
  if (!box) return
  const details = box.closest('details')
  if (details && !details.open) details.open = true
  // Wait a frame: a <details> that was just opened is not laid out yet, so it cannot be scrolled to or focused.
  requestAnimationFrame(() => {
    const select = box.querySelector('select')
    select?.scrollIntoView({ block: 'center' })
    select?.focus()
  })
}

// One muted line for Sections that use the Story tools engine: "Uses Ollama · Change".
export function EngineLine({ engines, engine }: { engines: TranslateEngine[]; engine: string }) {
  const current = engines.find((e) => e.name === engine)
  return (
    <p className="muted reader-engine-line">
      {current ? `Uses ${engineShortName(current)}` : 'No AI engine set up'}
      {' · '}
      <button type="button" className="link" onClick={focusPicker}>
        Change
      </button>
    </p>
  )
}
