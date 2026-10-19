import { useState } from 'react'

import { updateSourcesSettings } from '../../api/sources'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Section } from '../../components/Section'
import { Toggle } from '../../components/Toggle'
import { buttonClass } from '../../components/uiClasses'
import type { SourcesSettings } from '../../types/sources'
import { formatDecimalBytes } from '../libraryAdmin/libraryAdmin'
import { ManageCard } from './ManageCard'
import {
  CACHE_MAX_FIELD, DEVELOPER_FIELDS, PACING_GROUPS, PACING_ROWS, cacheLabel, draftFrom, pacingErrors, pacingSummary,
  settingsChanges, type NumField, type PacingDraft, type TimingKey,
} from './sourcesSettings'

type Props = {
  settings: SourcesSettings
  developerMode: boolean
  onSaved: (s: SourcesSettings) => void
  onClearCache: () => Promise<void>
  clearing: boolean
}

// Opens a closed fold when a field inside it turns invalid, so the message is not hidden.
function useOpenOnError(keys: string[]): number {
  const key = keys.join()
  const [seen, setSeen] = useState('')
  const [signal, setSignal] = useState(0)
  if (key !== seen) {
    setSeen(key)
    if (key) setSignal((n) => n + 1)
  }
  return signal
}

/** Timing & cache: one form, one Save, across the Pacing and Saved pages cards. */
export function TimingTab({ settings, developerMode, onSaved, onClearCache, clearing }: Props) {
  const [base, setBase] = useState(() => JSON.stringify(draftFrom(settings)))
  const [draft, setDraft] = useState<PacingDraft>(() => draftFrom(settings))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [saved, setSaved] = useState(false)
  // A fresh copy from the server resets the form, but Clear cache alone must not drop what is being typed.
  const serverKey = JSON.stringify(draftFrom(settings))
  if (serverKey !== base) {
    setBase(serverKey)
    setDraft(draftFrom(settings))
  }

  const errors = pacingErrors(draft)
  const changes = settingsChanges(settings, draft)
  const dirty = Object.keys(changes).length > 0
  const invalid = Object.keys(errors).length > 0
  const advancedKeys = PACING_ROWS.flat().map((f) => f.key).filter((k) => errors[k])
  const cacheKeys = (Object.keys(errors) as TimingKey[]).filter((k) => !advancedKeys.includes(k))
  const advancedSignal = useOpenOnError(advancedKeys)
  const cacheSignal = useOpenOnError(cacheKeys)

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

  const numInput = (f: NumField<TimingKey>) => (
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
  const where = [advancedKeys.length > 0 && 'Advanced timing', cacheKeys.length > 0 && 'Saved pages'].filter(Boolean)
  const cache = settings.cache

  return (
    <div className="sources-pacing">
      <ManageCard tab="timing" card="pacing" title="Pacing" summary={pacingSummary(settings)}>
        <p className="muted" data-testid="pacing-summary">{pacingSummary(settings)}</p>
        <Section title="Advanced timing" storageKey="sources.manage.timing.advanced" openSignal={advancedSignal}>
          {PACING_ROWS.map((row, i) => (
            <div className="field-row" role="group" aria-label={PACING_GROUPS[i]} key={row[0].key}>
              {row.map(numInput)}
            </div>
          ))}
        </Section>
      </ManageCard>

      <ManageCard
        tab="timing"
        card="saved-pages"
        title="Saved pages"
        summary={`${cacheLabel(settings.cache_mode)} · ${cache.entries ? formatDecimalBytes(cache.bytes) : 'empty'}`}
        openSignal={cacheSignal}
      >
        <div className="field-row">
          <Field label="Saved pages" help="Stops re-downloading the same page.">
            <select value={draft.cache_mode} onChange={(e) => set('cache_mode', e.target.value)}>
              {modes.map((m) => (
                <option key={m} value={m}>
                  {cacheLabel(m)}
                </option>
              ))}
            </select>
          </Field>
          {numInput(CACHE_MAX_FIELD)}
        </div>
        <div className="actions" data-testid="sources-cache">
          <span>
            Saved pages:{' '}
            {cache.entries ? `${cache.entries} item${cache.entries === 1 ? '' : 's'} · ${formatDecimalBytes(cache.bytes)}` : 'empty'}
          </span>
          {cache.entries > 0 && (
            <ConfirmButton label="Clear cache…" name="raw-content cache" verb="clear" busy={clearing} onConfirm={onClearCache} />
          )}
        </div>
      </ManageCard>

      {developerMode && (
        <ManageCard tab="timing" card="developer" title="Developer options">
          <div className="setting-list">
            {DEVELOPER_FIELDS.map((b) => (
              <Field key={b.key} label={b.label} help={b.help}>
                <Toggle checked={draft[b.key]} onChange={(on) => set(b.key, on)} />
              </Field>
            ))}
          </div>
        </ManageCard>
      )}

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
      {dirty && invalid && <p className="muted">Fix the values marked in {where.join(' and ')} first.</p>}
    </div>
  )
}
