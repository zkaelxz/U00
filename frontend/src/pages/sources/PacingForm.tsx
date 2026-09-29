import { useState } from 'react'

import { updateSourcesSettings } from '../../api/sources'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import { buttonClass } from '../../components/uiClasses'
import type { SourcesSettings } from '../../types/sources'
import {
  BOOL_FIELDS,
  CHECK_FIELD,
  PACING_ROWS,
  cacheLabel,
  draftFrom,
  pacingErrors,
  settingsChanges,
  type NumField,
  type PacingDraft,
} from './sourcesFormat'

type Props = {
  settings: SourcesSettings
  onSaved: (s: SourcesSettings) => void
}

/** Pacing & cache. Save sends only the changed keys (PC-only). */
export function PacingForm({ settings, onSaved }: Props) {
  const [base, setBase] = useState(settings)
  const [draft, setDraft] = useState<PacingDraft>(() => draftFrom(settings))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [saved, setSaved] = useState(false)
  // A fresh copy from the server (e.g. after Clear cache) resets the form.
  if (settings !== base) {
    setBase(settings)
    setDraft(draftFrom(settings))
  }

  const errors = pacingErrors(draft)
  const changes = settingsChanges(settings, draft)
  const dirty = Object.keys(changes).length > 0
  const invalid = Object.keys(errors).length > 0

  function set<K extends keyof PacingDraft>(key: K, value: PacingDraft[K]) {
    setSaved(false)
    setDraft((d) => ({ ...d, [key]: value }))
  }

  async function save() {
    if (!dirty || invalid) return
    setError(null)
    setSaving(true)
    try {
      const next = await updateSourcesSettings(changes)
      setSaved(true)
      onSaved(next)
    } catch (e) {
      setError(e)
    } finally {
      setSaving(false)
    }
  }

  const numInput = (f: NumField) => (
    <Field key={f.key} label={f.label} help={f.help} error={errors[f.key]}>
      <input
        type="number"
        inputMode={f.step && f.step % 1 !== 0 ? 'decimal' : 'numeric'}
        min={f.min}
        max={f.max}
        step={f.step ?? 1}
        value={draft[f.key]}
        onChange={(e) => set(f.key, e.target.value)}
      />
    </Field>
  )

  const modes = settings.cache_modes.includes(draft.cache_mode)
    ? settings.cache_modes
    : [...settings.cache_modes, draft.cache_mode]

  return (
    <div className="sources-pacing">
      {PACING_ROWS.map((row) => (
        <div className="field-row" key={row[0].key}>
          {row.map(numInput)}
        </div>
      ))}
      <div className="field-row">
        <Field label="Cache" help="Stops re-downloading the same page.">
          <select value={draft.cache_mode} onChange={(e) => set('cache_mode', e.target.value)}>
            {modes.map((m) => (
              <option key={m} value={m}>
                {cacheLabel(m)}
              </option>
            ))}
          </select>
        </Field>
        {numInput(CHECK_FIELD)}
      </div>
      <div className="setting-list">
        {BOOL_FIELDS.map((b) => (
          <Field key={b.key} label={b.label} help={b.help}>
            <Toggle checked={draft[b.key]} onChange={(on) => set(b.key, on)} />
          </Field>
        ))}
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ pcOnly: true, serverText: true }} />
      <div className="actions">
        <button type="button" className={buttonClass(dirty && !invalid ? 'primary' : 'secondary')} disabled={!dirty || invalid || saving} onClick={save}>
          {saving ? 'Saving…' : 'Save settings'}
        </button>
        <span aria-live="polite" className="muted">
          {saved && !dirty ? 'Saved.' : ''}
        </span>
      </div>
      {!dirty && !saved && <p className="muted">No changes to save.</p>}
      {dirty && invalid && <p className="muted">Fix the values marked above first.</p>}
    </div>
  )
}
