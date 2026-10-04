// Turn the independent review role on and pick its engine. Off by
// default. When on, every proposed fix is checked by a different engine and
// both views are shown; nothing is applied or hidden either way.
import { useState } from 'react'

import { saveAssistantSettings } from '../../api/assistant'
import { Field } from '../../components/Field'
import { humanize } from '../../components/labels'
import { Section } from '../../components/Section'
import { Toggle } from '../../components/Toggle'
import { buttonClass } from '../../components/uiClasses'
import type { AssistantSettings, AssistantSettingsPatch } from '../../types/assistant'
import { assistantErrorText } from './assistantFormat'
import { CloudConsent } from './CloudConsent'
import { reviewEngineProblem } from './reviewFormat'

type Props = {
  settings: AssistantSettings
  implementEngine: string
  onSettings: (s: AssistantSettings) => void
}

export function ReviewRolesSection({ settings, implementEngine, onSettings }: Props) {
  const [reviewEngine, setReviewEngine] = useState(settings.review_engine ?? '')
  const [reviewModel, setReviewModel] = useState(settings.review_model ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const on = settings.roles_enabled === true
  const answering = implementEngine || settings.engine || 'claude'
  const problem = on ? reviewEngineProblem(answering, reviewEngine) : null

  const save = (patch: AssistantSettingsPatch) => {
    setBusy(true)
    setError(null)
    saveAssistantSettings(patch).then(
      (s) => {
        setBusy(false)
        onSettings(s)
      },
      (e: unknown) => {
        setBusy(false)
        setError(assistantErrorText(e))
      },
    )
  }

  const changed =
    reviewEngine !== (settings.review_engine ?? '') || reviewModel.trim() !== (settings.review_model ?? '')

  return (
    <Section
      title="Independent review"
      summary={on ? `On · ${reviewEngine ? humanize('engine', reviewEngine) : 'no engine'}` : 'Off'}
      storageKey="assistant.review"
    >
      <Field
        label="Review each proposed fix"
        help="A second, different engine checks every proposed fix with the same read-only tools. You see both views."
      >
        <Toggle checked={on} disabled={busy} onChange={(next) => save({ roles_enabled: next })} />
      </Field>
      <div className="assistant-engine">
        <Field label="Review engine" help="Must be a different engine from the one that answers.">
          <select value={reviewEngine} onChange={(e) => setReviewEngine(e.target.value)}>
            <option value="">None</option>
            {settings.engine_choices.map((c) => (
              <option key={c} value={c}>
                {humanize('engine', c)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Review model" help="Optional. Leave blank for the engine's default model.">
          <input
            type="text"
            value={reviewModel}
            spellCheck={false}
            autoComplete="off"
            placeholder="Engine default"
            onChange={(e) => setReviewModel(e.target.value)}
          />
        </Field>
      </div>
      <div className="assistant-actions">
        <button
          type="button"
          className={buttonClass('secondary', 'sm')}
          disabled={busy || !changed}
          onClick={() => save({ review_engine: reviewEngine || null, review_model: reviewModel.trim() || null })}
        >
          {busy ? 'Saving…' : 'Save review engine'}
        </button>
      </div>
      {problem && <p className="muted">{problem}</p>}
      {settings.review_engine && <CloudConsent settings={settings} onSettings={onSettings} only={[settings.review_engine]} />}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </Section>
  )
}
