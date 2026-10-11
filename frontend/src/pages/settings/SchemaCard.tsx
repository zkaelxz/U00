/*
 * A Settings card or fold whose fields come from the declared settings
 * (GET /api/settings/schema): one field per row of a `section`, with the
 * label, help, unit, limits and choices the row declares. Values are read from
 * and saved through the overview (GET/POST /api/settings), so declaring a row
 * is all it takes to add a field here.
 *
 * Away from the PC the block shows "PC only", like every preference.
 */
import { useState, type ReactNode } from 'react'

import { updatePreferences } from '../../api/settings'
import { Card } from '../../components/Card'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { humanize, humanizeValue } from '../../components/labels'
import { Section } from '../../components/Section'
import { Toggle } from '../../components/Toggle'
import { buttonClass } from '../../components/uiClasses'
import { PC_ONLY_BODY, PC_ONLY_SUMMARY, usePcOnly } from '../../hooks/usePcOnly'
import type { SettingsOverview, SettingsPreferences, SettingsSchemaRow } from '../../types/settings'
import { useDeveloperMode } from '../assistant/developerMode'
import { changedPreferences, checkPath, OCR_LABELS, type Parsed } from './preferences'

const grid = { display: 'grid', gap: 'var(--space-3)' } as const

type Draft = Record<string, string | boolean>

export type SchemaCardProps = {
  schema: SettingsSchemaRow[] | null
  settings: SettingsOverview
  onSettings: (s: SettingsOverview) => void
  section: string
  // Limits the block to these rows, in this order; default: every row of the section, as declared.
  keys?: string[]
  as?: 'card' | 'section'
  title: string
  summary: string
  openSignal?: number
  // Shown above / below the fields, inside the form.
  before?: ReactNode
  after?: ReactNode
  // A row the environment decides: shown as this text, not editable.
  locked?: Record<string, string>
  helpOverride?: Record<string, string>
  placeholderOverride?: Record<string, string>
}

function rowsFor(schema: SettingsSchemaRow[] | null, section: string, keys: string[] | undefined, developerMode: boolean) {
  const inSection = (schema ?? []).filter(
    (r) => r.section === section && !r.custom && (developerMode || !r.dev_only),
  )
  if (!keys) return inSection
  return keys.flatMap((k) => inSection.filter((r) => r.key === k))
}

function savedValue(settings: SettingsOverview, key: string): unknown {
  const prefs = settings.preferences as unknown as Record<string, unknown>
  return key in prefs ? prefs[key] : (settings as unknown as Record<string, unknown>)[key]
}

// A zero or unset number reads as blank: the placeholder says what blank means.
export function toDraftValue(row: SettingsSchemaRow, saved: unknown): string | boolean {
  if (row.type === 'bool') return saved === true
  if (row.type === 'int' || row.type === 'float') return saved == null || saved === 0 ? '' : String(saved)
  if (row.type === 'json') return JSON.stringify(saved ?? row.default ?? {}, null, 2)
  return saved == null ? '' : String(saved)
}

export function toSavedValue(row: SettingsSchemaRow, raw: string | boolean): Parsed<unknown> {
  if (row.type === 'bool') return { ok: true, value: raw === true }
  const text = String(raw).trim()
  if (row.type === 'choice') return { ok: true, value: text || row.default }
  if (row.type === 'int' || row.type === 'float') {
    if (!text) return { ok: true, value: row.default }
    const whole = row.type === 'int'
    const range =
      row.min != null && row.max != null ? ` from ${row.min.toLocaleString('en-US')} to ${row.max.toLocaleString('en-US')}` : ''
    const n = Number(text)
    if (!(whole ? /^\d+$/ : /^\d+(\.\d+)?$/).test(text) || (row.min != null && n < row.min) || (row.max != null && n > row.max)) {
      return { ok: false, error: whole ? `Enter a whole number${range}.` : `Enter an amount${range}.` }
    }
    return { ok: true, value: n }
  }
  if (row.type === 'json') {
    try {
      return { ok: true, value: JSON.parse(text) }
    } catch {
      return { ok: false, error: `${row.label} is not valid JSON.` }
    }
  }
  if (row.max_len != null && text.length > row.max_len) return { ok: false, error: `${row.label} is too long.` }
  if (row.type === 'path') {
    const problem = checkPath(text)
    if (problem) return { ok: false, error: problem }
  }
  return { ok: true, value: text }
}

// The server sends the stored value as the label; the page words it.
function choiceLabel(row: SettingsSchemaRow, value: string): string {
  if (row.key === 'ocr_backend') return OCR_LABELS[value] ?? humanizeValue(value)
  if (row.key === 'default_locale') return humanize('locale', value)
  return humanizeValue(value)
}

export function SchemaCard(props: SchemaCardProps) {
  const { schema, settings, onSettings, section, keys, as = 'section', title, summary, openSignal } = props
  const developerMode = useDeveloperMode()
  const remote = usePcOnly() === 'remote'
  const rows = rowsFor(schema, section, keys, developerMode)
  const fromSettings = (s: SettingsOverview): Draft =>
    Object.fromEntries(rows.map((r) => [r.key, toDraftValue(r, savedValue(s, r.key))]))

  const [draft, setDraft] = useState<Draft | null>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)

  if (remote) {
    return (
      <Block as={as} openSignal={openSignal} title={title} summary={PC_ONLY_SUMMARY}>
        <p className="muted">{PC_ONLY_BODY}</p>
      </Block>
    )
  }

  const base = fromSettings(settings)
  // The schema arrives after the page; edits start from the saved values until the first change.
  const current: Draft = { ...base, ...draft }
  const dirty = rows.some((r) => base[r.key] !== current[r.key])
  const set = (key: string, value: string | boolean) => {
    setDraft({ ...current, [key]: value })
    setNote(null)
    setProblem(null)
  }
  const reset = () => {
    setDraft(null)
    setProblem(null)
  }
  const save = () => {
    const patch: Record<string, unknown> = {}
    for (const r of rows) {
      if (props.locked && r.key in props.locked) continue
      const parsed = toSavedValue(r, current[r.key])
      if (!parsed.ok) {
        setProblem(parsed.error)
        return
      }
      patch[r.key] = parsed.value
    }
    const changed = changedPreferences(settings.preferences, patch as Partial<SettingsPreferences>)
    if (Object.keys(changed).length === 0) {
      reset()
      setNote('Nothing to change.')
      return
    }
    setBusy(true)
    setError(null)
    updatePreferences(changed).then(
      (s) => {
        setBusy(false)
        setDraft(null)
        setNote('Saved.')
        onSettings(s)
      },
      (e: unknown) => {
        setBusy(false)
        setError(e)
      },
    )
  }

  return (
    <Block as={as} openSignal={openSignal} title={title} summary={summary}>
      <div style={grid}>
        <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ pcOnly: true }} />
        {props.before}
        {rows.map((row) => (
          <SchemaField key={row.key} row={row} props={props} value={current[row.key]} onChange={(v) => set(row.key, v)} />
        ))}
        {props.after}
        {problem && <p className="error" role="alert">{problem}</p>}
        <div className="settings-actions">
          {/* Save is a Card's main action; the Advanced Card holds several, so there it stays secondary. */}
          <button type="button" className={buttonClass(as === 'card' ? 'primary' : 'secondary')} disabled={busy || !dirty} onClick={save}>
            {busy ? 'Saving…' : 'Save'}
          </button>
          {dirty && !busy && (
            <button type="button" className={buttonClass('ghost')} onClick={reset}>
              Undo changes
            </button>
          )}
          <span className="muted" role="status">{note ?? ''}</span>
        </div>
      </div>
    </Block>
  )
}

function SchemaField({ row, props, value, onChange }: { row: SettingsSchemaRow; props: SchemaCardProps; value: string | boolean; onChange: (v: string | boolean) => void }) {
  const locked = props.locked?.[row.key]
  const help = props.helpOverride?.[row.key] ?? row.help
  const placeholder = props.placeholderOverride?.[row.key] ?? row.placeholder
  const text = String(value)
  const common = { value: locked ?? text, disabled: locked !== undefined, placeholder: placeholder || undefined }
  let control: ReactNode
  if (row.type === 'bool') {
    control = <Toggle checked={value === true} onChange={onChange} />
    return (
      <div className="setting-list">
        <Field label={row.label} help={help || undefined}>{control}</Field>
      </div>
    )
  }
  if (row.type === 'choice') {
    control = (
      <select value={text} onChange={(e) => onChange(e.target.value)}>
        {row.default == null && <option value="">None</option>}
        {(row.choices ?? []).map((c) => (
          <option key={c.value} value={c.value}>{choiceLabel(row, c.value)}</option>
        ))}
      </select>
    )
  } else if (row.type === 'json' || row.multiline) {
    control = (
      <textarea
        rows={row.type === 'json' ? 6 : 2}
        maxLength={row.max_len ?? undefined}
        spellCheck={row.type !== 'json'}
        {...common}
        onChange={(e) => onChange(e.target.value)}
      />
    )
  } else {
    const numeric = row.type === 'int' || row.type === 'float'
    // type="text": a number input would swallow a typo and send nothing, so the
    // message under the field could never say what was wrong.
    control = (
      <input
        type="text"
        inputMode={row.type === 'int' ? 'numeric' : row.type === 'float' ? 'decimal' : undefined}
        spellCheck={numeric || row.type === 'path' ? false : undefined}
        {...common}
        onChange={(e) => onChange(e.target.value)}
      />
    )
  }
  return (
    <Field label={row.label} unit={row.unit || undefined} help={help || undefined}>
      {control}
    </Field>
  )
}

// A Card (always open; the summary is its meta line) or a Section fold.
function Block({ as, openSignal, title, summary, children }: { as: 'card' | 'section'; openSignal?: number; title: string; summary: string; children: ReactNode }) {
  if (as === 'card')
    return (
      <Card title={title} meta={summary} aria-label={title}>
        {children}
      </Card>
    )
  return (
    <Section title={title} summary={summary} openSignal={openSignal}>
      {children}
    </Section>
  )
}
