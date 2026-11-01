import { useEffect, useState } from 'react'

import { getInstructions, saveInstructions } from '../../api/translateStage'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'

// Same heading as services/glossary_common.TERM_CONVENTIONS_HEADING: conventions are the
// trailing section of the one instructions text, so the translate prompt and the glossary
// extraction both read them from where they already read instructions.
const HEADING = 'Term conventions:'

export function splitConventions(text: string): { prose: string; conventions: string } {
  const at = `\n${text}`.lastIndexOf(`\n${HEADING}\n`)
  if (at < 0) return { prose: text.trim(), conventions: '' }
  return { prose: text.slice(0, Math.max(at - 1, 0)).trim(), conventions: text.slice(at + HEADING.length + 1).trim() }
}

export function joinConventions(prose: string, conventions: string): string {
  const p = prose.trim()
  const c = conventions.trim()
  if (!c) return p
  return `${p ? `${p}\n\n` : ''}${HEADING}\n${c}`
}

export function InstructionsForm({ id, saved, onSaved }: { id: number; saved: string; onSaved: (text: string) => void }) {
  const start = splitConventions(saved)
  const [prose, setProse] = useState(start.prose)
  const [conventions, setConventions] = useState(start.conventions)
  const [done, setDone] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const edit = (set: (v: string) => void) => (e: { target: { value: string } }) => {
    setDone(false)
    set(e.target.value)
  }
  return (
    <div className="stack">
      <Field label="Instructions">
        <textarea rows={5} value={prose} onChange={edit(setProse)} />
      </Field>
      <Field label="Term conventions">
        <textarea rows={4} value={conventions} onChange={edit(setConventions)} />
      </Field>
      <div className="actions">
        <button
          type="button"
          className={buttonClass('secondary', 'sm')}
          onClick={() => {
            const text = joinConventions(prose, conventions)
            saveInstructions(id, 'project', text).then(() => {
              setError(null)
              setDone(true)
              onSaved(text)
            }, setError)
          }}
        >
          Save
        </button>
        {done && <span role="status">Saved.</span>}
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </div>
  )
}

export function NovelInstructionsTab({ id }: { id: number }) {
  const [saved, setSaved] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  useEffect(() => {
    let cancelled = false
    getInstructions(id).then(
      (i) => !cancelled && setSaved(i.project_instructions),
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [id])
  return (
    <section className="panel" aria-label="Instructions">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {saved !== null && <InstructionsForm id={id} saved={saved} onSaved={setSaved} />}
    </section>
  )
}
