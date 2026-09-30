// What the assistant can read with. All read-only in this build.
import { useEffect, useState } from 'react'

import { getAssistantTools } from '../../api/assistant'
import { Card } from '../../components/Card'
import { Section } from '../../components/Section'
import type { AssistantToolList } from '../../types/assistant'
import { READ_ONLY_NOTE, assistantErrorText, plural } from './assistantFormat'

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
    <Card title="Tools" meta={tools ? `${plural(tools.tools.length, 'read-only tool')}` : undefined} aria-label="Tools">
      <p className="muted" data-testid="read-only-note">
        {READ_ONLY_NOTE}
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {tools && tools.tools.length > 0 && (
        <Section title="What it can read" storageKey="assistant.tools">
          <ul className="assistant-tools" aria-label="Assistant tools">
            {tools.tools.map((t) => (
              <li key={t.name}>
                <code>{t.name}</code>
                <span className="muted">{t.description}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}
    </Card>
  )
}
