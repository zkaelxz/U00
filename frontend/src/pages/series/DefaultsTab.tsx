import { useCallback, useState } from 'react'

import {
  applySeriesDefaults, getDefaultsPreview, getSeriesDefaults, saveSeriesDefaults,
  type ApplyMode, type SeriesDefaults, type SeriesDefaultsField, type SeriesDefaultsPreview, type SeriesPageData,
} from '../../api/series'
import { Card } from '../../components/Card'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'
import { dramaName } from '../../components/libraryView'
import { useLoad } from '../../hooks/useLoad'
import { defaultsForm, defaultsPreviewText, settingsFromForm, shownValue, type DefaultsForm } from './seriesModel'

const GROUPS: { id: string; title: string }[] = [
  { id: 'translate', title: 'Translate' },
  { id: 'transcribe', title: 'Transcribe' },
]

// Field hands its control an id and aria attributes; they go on the real input.
function SettingControl({ field, value, disabled, onChange, ...aria }: {
  field: SeriesDefaultsField
  value: string
  disabled: boolean
  onChange: (v: string) => void
  id?: string
  'aria-describedby'?: string
  'aria-invalid'?: boolean
}) {
  if (field.type === 'bool' || field.type === 'choice') {
    const options = field.type === 'bool' ? [['true', 'On'], ['false', 'Off']] : (field.choices ?? []).map((c) => [c, c])
    return (
      <select {...aria} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
        <option value="">Not set</option>
        {options.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
      </select>
    )
  }
  return (
    <input
      {...aria}
      type="number"
      inputMode="decimal"
      value={value}
      placeholder="Not set"
      min={field.min ?? undefined}
      max={field.max ?? undefined}
      step={field.type === 'int' ? 1 : 'any'}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    />
  )
}

function SettingsCard({ data, defaults, onSaved }: { data: SeriesPageData; defaults: SeriesDefaults; onSaved: () => void }) {
  const [form, setForm] = useState<DefaultsForm>(() => defaultsForm(defaults.fields, defaults.settings))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const readOnly = !data.can_edit

  const save = () => {
    const out = settingsFromForm(defaults.fields, form)
    if ('problem' in out) {
      setProblem(out.problem)
      return
    }
    setProblem(null)
    setBusy(true)
    saveSeriesDefaults(data.id, out.settings).then(
      () => { setBusy(false); setError(null); setSaved(true); onSaved() },
      (e: unknown) => { setBusy(false); setError(e) },
    )
  }

  return (
    <Card title="Default title settings" className="series-defaults-card">
      <p className="muted">
        A new title in this series starts with these. A title moved into the series gets them only for settings it has
        no value for. Not set: the title keeps the app's own default. API keys and the paid-only switches (Groq
        transcription, Thinking) stay per title.
      </p>
      {readOnly && <p className="muted">You can't change this series' defaults.</p>}
      {GROUPS.map((g) => (
        <fieldset key={g.id} className="series-defaults-group">
          <legend>{g.title}</legend>
          <div className="series-defaults-grid">
            {defaults.fields.filter((f) => f.group === g.id).map((f) => (
              <Field key={f.key} label={f.label}>
                <SettingControl
                  field={f}
                  value={form[f.key] ?? ''}
                  disabled={busy || readOnly}
                  onChange={(v) => { setSaved(false); setForm((x) => ({ ...x, [f.key]: v })) }}
                />
              </Field>
            ))}
          </div>
        </fieldset>
      ))}
      {!readOnly && (
        <div className="actions">
          <button type="button" className={buttonClass('primary')} disabled={busy} onClick={save}>
            {busy ? 'Working…' : 'Save defaults'}
          </button>
          {saved && <span className="muted" role="status" data-testid="series-defaults-saved">Saved.</span>}
        </div>
      )}
      {problem && <p className="error" role="alert">{problem}</p>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </Card>
  )
}

function ApplyCard({ data, defaults }: { data: SeriesPageData; defaults: SeriesDefaults }) {
  const [mode, setMode] = useState<ApplyMode>('fill')
  const [preview, setPreview] = useState<SeriesDefaultsPreview | null>(null)
  const [result, setResult] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const labels = new Map(defaults.fields.map((f) => [f.key, f.label]))
  const nothingSet = Object.keys(defaults.settings).length === 0

  const run = <T,>(p: Promise<T>, done: (v: T) => void) => {
    setBusy(true)
    setError(null)
    p.then((v) => { setBusy(false); done(v) }, (e: unknown) => { setBusy(false); setError(e) })
  }
  const pick = (m: ApplyMode) => { setMode(m); setPreview(null); setResult(null) }

  if (!data.can_edit) return null
  return (
    <Card title="Apply to all titles in this series" className="series-defaults-apply">
      {nothingSet ? (
        <p className="muted">Save at least one default first.</p>
      ) : (
        <>
          <fieldset className="series-defaults-mode">
            <legend className="visually-hidden">Which settings to change</legend>
            <label>
              <input type="radio" name="defaults-mode" checked={mode === 'fill'} onChange={() => pick('fill')} />
              {' '}Only fill settings a title hasn't changed (a value equal to the app's default counts as unchanged)
            </label>
            <label>
              <input type="radio" name="defaults-mode" checked={mode === 'overwrite'} onChange={() => pick('overwrite')} />
              {' '}Overwrite every title's settings
            </label>
          </fieldset>
          <div className="actions">
            <button
              type="button"
              className={buttonClass('secondary')}
              disabled={busy}
              onClick={() => run(getDefaultsPreview(data.id, mode), (p) => { setPreview(p); setResult(null) })}
            >
              Preview
            </button>
          </div>
        </>
      )}
      {preview && (
        <div className="series-defaults-preview" data-testid="series-defaults-preview">
          <p role="status">{defaultsPreviewText(preview)}</p>
          <ul>
            {preview.titles.filter((t) => Object.keys(t.changes).length || t.unsuited.length).map((t) => (
              <li key={t.id}>
                <strong>{dramaName(t)}</strong>
                {Object.keys(t.changes).length > 0 && (
                  <>: {Object.entries(t.changes).map(([k, c]) => `${labels.get(k) ?? k} ${shownValue(c.from)} → ${shownValue(c.to)}`).join(' · ')}</>
                )}
                {t.unsuited.length > 0 && (
                  <span className="muted"> (skipped for its language: {t.unsuited.map((k) => labels.get(k) ?? k).join(', ')})</span>
                )}
              </li>
            ))}
          </ul>
          {preview.job_running && (
            <p className="muted">A job is running for a title in this series. Wait for it to finish or cancel it first.</p>
          )}
          {preview.changed_titles > 0 && (
            <div className="actions">
              <button
                type="button"
                className={buttonClass('primary')}
                disabled={busy || preview.job_running}
                onClick={() => run(applySeriesDefaults(data.id, mode), (r) => {
                  setPreview(null)
                  setResult(`Changed ${r.fields_changed === 1 ? '1 setting' : `${r.fields_changed} settings`} on ${r.titles_changed === 1 ? '1 title' : `${r.titles_changed} titles`}.`)
                })}
              >
                {busy ? 'Working…' : `Apply to ${preview.changed_titles === 1 ? '1 title' : `${preview.changed_titles} titles`}`}
              </button>
              <button type="button" className={buttonClass('ghost')} disabled={busy} onClick={() => setPreview(null)}>Cancel</button>
            </div>
          )}
        </div>
      )}
      {result && <p className="status-line" role="status" data-testid="series-defaults-result">{result}</p>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </Card>
  )
}

export function DefaultsTab({ data }: { data: SeriesPageData }) {
  const [reloadKey, setReloadKey] = useState(0)
  const load = useCallback(() => getSeriesDefaults(data.id), [data.id])
  const defaults = useLoad(load, reloadKey)
  return (
    <>
      <ErrorBanner error={defaults.error} />
      {!defaults.data && !defaults.error && <p className="muted">Loading…</p>}
      {defaults.data && (
        <>
          <SettingsCard data={data} defaults={defaults.data} onSaved={() => setReloadKey((k) => k + 1)} />
          <ApplyCard key={`apply-${reloadKey}`} data={data} defaults={defaults.data} />
        </>
      )}
    </>
  )
}
