import { useState } from 'react'

import { saveTranslatePreset, updateTranslatePreset } from '../../api/translateStage'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Section } from '../../components/Section'
import { Toggle } from '../../components/Toggle'
import { buttonClass } from '../../components/uiClasses'
import { engineLabel } from '../../labels'
import type { LibraryPreset, ProfileField } from '../../types/library'
import type { TranslateEngine } from '../../types/translate'
import { toBody, validateProfile, MAX_FALLBACKS, type ProfileForm } from './profileForm'

export interface ProfileEditorData {
  engines: TranslateEngine[]
  style_presets: { key: string; label: string }[]
  fields: ProfileField[]
}

const NOT_SET = 'Not set'

function TitleOption({ spec, value, onChange }: { spec: ProfileField; value: string; onChange: (v: string) => void }) {
  if (spec.type === 'bool') {
    return (
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{NOT_SET}</option>
        <option value="true">On</option>
        <option value="false">Off</option>
      </select>
    )
  }
  if (spec.type === 'choice') {
    return (
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{NOT_SET}</option>
        {spec.choices?.map((c) => <option key={c} value={c}>{c}</option>)}
      </select>
    )
  }
  return (
    <input
      inputMode="decimal"
      value={value}
      placeholder={`${spec.min} to ${spec.max}`}
      onChange={(e) => onChange(e.target.value)}
    />
  )
}

/**
 * Create or edit one translation profile: every field of the Translate form that carries over,
 * plus the transcribe options. It never holds the source language, the English variant or
 * a glossary. Editing replaces all fields (PC only); a new profile is saved under a new name.
 */
export function ProfileEditor({ data, initial, existing, onSaved, onCancel }: {
  data: ProfileEditorData
  initial: ProfileForm
  existing: LibraryPreset | null
  onSaved: () => void
  onCancel: () => void
}) {
  const [f, setF] = useState<ProfileForm>(initial)
  const [error, setError] = useState<unknown>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const set = <K extends keyof ProfileForm>(k: K, v: ProfileForm[K]) => setF((s) => ({ ...s, [k]: v }))
  const engine = data.engines.find((e) => e.name === f.engine)
  const models = engine?.models ?? []
  const others = data.engines.filter((e) => e.name !== f.engine)
  const groups = (g: ProfileField['group']) => data.fields.filter((s) => s.group === g)

  const save = () => {
    const bad = validateProfile(f, data.fields)
    setProblem(bad)
    if (bad) return
    setBusy(true)
    setError(null)
    const body = toBody(f, data.fields)
    const done = existing ? updateTranslatePreset(existing.id, body) : saveTranslatePreset(body)
    done.then(() => { setBusy(false); onSaved() }, (e: unknown) => { setBusy(false); setError(e) })
  }

  return (
    <div className="stack profile-editor" role="group" aria-label={existing ? `Edit profile ${existing.name}` : 'New profile'}>
      <Field label="Profile name">
        <input value={f.name} maxLength={100} onChange={(e) => set('name', e.target.value)} />
      </Field>
      <p className="muted">
        A profile fills in the settings below when you pick it for a title. It never changes the
        title&apos;s language or English variant, and it has no glossary. Leave an option unset to
        keep what the title already has.
      </p>
      <Section title="Translation" summary="Engine, model, style and run options" defaultOpen>
        <div className="field-row">
          <Field label="Engine">
            <select value={f.engine} onChange={(e) => setF((s) => ({ ...s, engine: e.target.value, model: '', fallbacks: [] }))}>
              {data.engines.map((e) => <option key={e.name} value={e.name}>{engineLabel(e.name)}</option>)}
            </select>
          </Field>
          <Field label="Model">
            <select value={f.model} disabled={!models.length} onChange={(e) => set('model', e.target.value)}>
              <option value="">Engine default</option>
              {f.model && !models.includes(f.model) && <option value={f.model}>{f.model}</option>}
              {models.map((m) => <option key={m} value={m}>{engine?.model_labels?.[m] ?? m}</option>)}
            </select>
          </Field>
          <Field label="Style">
            <select value={f.style_preset} onChange={(e) => set('style_preset', e.target.value)}>
              {data.style_presets.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
            </select>
          </Field>
        </div>
        <Field label="Style note" help="Extra instructions added to every translation prompt.">
          <textarea rows={2} maxLength={4000} value={f.style_note} onChange={(e) => set('style_note', e.target.value)} />
        </Field>
        <div className="field-row">
          <Field label="Genre notes in the prompt"><Toggle checked={f.genre_notes} onChange={(v) => set('genre_notes', v)} /></Field>
          <Field label="Default to she/her"><Toggle checked={f.female_pronouns} onChange={(v) => set('female_pronouns', v)} /></Field>
          <Field label="Reflect"><Toggle checked={f.reflect} onChange={(v) => set('reflect', v)} /></Field>
          <Field label="Bulk"><Toggle checked={f.bulk} onChange={(v) => set('bulk', v)} /></Field>
          <Field label="Think harder"><Toggle checked={f.thinking} onChange={(v) => set('thinking', v)} /></Field>
        </div>
        <div className="field-row">
          <Field label="Batch size" help="Blank = the engine's default."><input inputMode="numeric" value={f.batch_size} onChange={(e) => set('batch_size', e.target.value)} /></Field>
          <Field label="Context window"><input inputMode="numeric" value={f.context_window} onChange={(e) => set('context_window', e.target.value)} /></Field>
          <Field label="Context window ahead"><input inputMode="numeric" value={f.context_window_ahead} onChange={(e) => set('context_window_ahead', e.target.value)} /></Field>
          <Field label="Cost cap per job ($)" help="Blank = no cap."><input inputMode="decimal" value={f.cost_cap} onChange={(e) => set('cost_cap', e.target.value)} /></Field>
        </div>
        <div className="field-row">
          {Array.from({ length: MAX_FALLBACKS }, (_, i) => (
            <Field key={i} label={`Fallback engine ${i + 1}`}>
              <select
                value={f.fallbacks[i] ?? ''}
                disabled={i > f.fallbacks.length}
                onChange={(e) => set('fallbacks', [...f.fallbacks.slice(0, i), ...(e.target.value ? [e.target.value] : [])])}
              >
                <option value="">None</option>
                {others.filter((o) => o.name === f.fallbacks[i] || !f.fallbacks.includes(o.name)).map((o) => (
                  <option key={o.name} value={o.name}>{engineLabel(o.name)}</option>
                ))}
              </select>
            </Field>
          ))}
        </div>
      </Section>
      <Section title="Transcription" summary="Whisper model, recognizer, sensitivity and the rest">
        <p className="muted">An option that does not suit a title&apos;s language is skipped for that title.</p>
        <div className="field-row">
          {[...groups('transcribe'), ...groups('translate')].map((spec) => (
            <Field key={spec.key} label={spec.label}>
              <TitleOption spec={spec} value={f.title[spec.key] ?? ''} onChange={(v) => setF((s) => ({ ...s, title: { ...s.title, [spec.key]: v } }))} />
            </Field>
          ))}
        </div>
      </Section>
      {problem && <p className="error" role="alert">{problem}</p>}
      <ErrorBanner error={error} describe={{ pcOnly: true }} />
      <div className="actions">
        <button type="button" className={buttonClass('primary')} disabled={busy} onClick={save}>
          {existing ? 'Save changes' : 'Save profile'}
        </button>
        <button type="button" className={buttonClass('ghost')} disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
    </div>
  )
}
