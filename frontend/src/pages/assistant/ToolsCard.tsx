// What the assistant can read with. All read-only in this build.
import { useEffect, useState } from 'react'

import { getAssistantTools } from '../../api/assistant'
import { Section } from '../../components/Section'
import type { AssistantToolList } from '../../types/assistant'
import { READ_ONLY_NOTE, assistantErrorText } from './assistantFormat'
import { plural } from '../../labels'

export function ToolsCard() {
  const [tools, setTools] = useState<AssistantToolList | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    getAssistantTools().then(
      (t) => live && setTools(t),
      (e: unknown) => live && setError(assistantErrorText(e)),
    )
    return () => {
      live = false
    }
  }, [])

  return (
    <section aria-label="Tools">
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <Section title="Tools" summary={tools ? plural(tools.tools.length, 'read-only tool') : 'What it can read'} storageKey="assistant.tools" defaultOpen>
      <p className="muted" data-testid="read-only-note">
        {READ_ONLY_NOTE}
      </p>
      {tools && tools.tools.length > 0 && (
          <ul className="assistant-tools" aria-label="Assistant tools">
            {tools.tools.map((t) => (
              <li key={t.name}>
                <code>{t.name}</code>
                <span className="muted">{t.description}</span>
              </li>
            ))}
          </ul>
      )}
      </Section>
    </section>
  )
}
