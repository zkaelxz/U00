import { useState } from 'react'

import { saveInstructions } from '../../../api/translateStage'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { Section } from '../../../components/Section'
import { buttonClass } from '../../../components/uiClasses'
import { useStage } from '../StageContext'
import { instructionsSummary } from './glossaryTabs'

const LABEL = { project: 'Project instructions', series: 'Series instructions' } as const

// One instructions text (this title's, or its series') with its own Save. `saved` is what the
// server holds, so the summary and the "set" line don't claim text that was never saved.
export function InstructionsEditor({ scope, initial, saved, onSaved, defaultOpen }: {
  scope: 'project' | 'series'
  initial: string
  saved: string
  onSaved: (text: string) => void
  defaultOpen?: boolean
}) {
  const { dramaId } = useStage()
  const [text, setText] = useState(initial)
  const [done, setDone] = useState(false)
  const [error, setError] = useState<unknown>(null)
  return (
    <Section
      storageKey={`glossary.instructions.${scope}`}
      title={LABEL[scope]}
      defaultOpen={defaultOpen}
      summary={instructionsSummary(saved)}
    >
      <div className="stack">
        <Field label={LABEL[scope]}>
          <textarea
            rows={3}
            value={text}
            onChange={(e) => {
              setDone(false)
              setText(e.target.value)
            }}
          />
        </Field>
        <div className="actions">
          <span className="muted" data-testid={`${scope}-instructions-count`}>
            {text.length} character{text.length === 1 ? '' : 's'}
          </span>
          <button
            type="button"
            className={buttonClass('secondary', 'sm')}
            onClick={() =>
              saveInstructions(dramaId, scope, text).then(() => {
                setError(null)
                setDone(true)
                onSaved(text)
              }, setError)
            }
          >
            Save {scope} instructions
          </button>
          {done && <span role="status">Saved.</span>}
        </div>
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
      </div>
    </Section>
  )
}
