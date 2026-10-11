/*
 * Settings > Timing and splitting: the thresholds that decide where speech is found,
 * how long lines are cut and how fragments are grouped into sentences. One Card, three
 * groups; each row shows its current value, its default, a plain tooltip and a
 * Reset button. Rows come from the declared settings (GET /api/settings/schema), so a
 * Developer Mode row is only listed while Developer Mode is on.
 *
 * A changed value only affects runs started afterwards; nothing rewrites saved lines.
 * Away from the PC the card shows "PC only", like every preference.
 */
import { useState } from 'react'

import { updatePreferences } from '../../api/settings'
import { Card } from '../../components/Card'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'
import { PC_ONLY_BODY, PC_ONLY_SUMMARY, usePcOnly } from '../../hooks/usePcOnly'
import type { SettingsOverview, SettingsPreferences, SettingsSchemaRow } from '../../types/settings'
import { useDeveloperMode } from '../assistant/developerMode'
import { toSavedValue } from './SchemaCard'
import {
  DEV_ONLY_NOTE, TIMING_NOTE, defaultLine, draftText, isDefault, resetPatch, savedNumber, timingGroups,
  type TimingGroup,
} from './timingSettings'

const TITLE = 'Timing and splitting'
const SUMMARY = 'Where speech is found, how lines are cut, how sentences are grouped'

export interface TimingViewProps {
  groups: TimingGroup[]
  saved: Record<string, number>
  draft: Record<string, string>
  errors: Record<string, string>
  busy: boolean
  note: string | null
  developerMode: boolean
  error?: unknown
  onDismissError?: () => void
  onChange: (key: string, text: string) => void
  onReset: (row: SettingsSchemaRow) => void
  onSave: () => void
  onUndo: () => void
}

export function TimingSettingsView(p: TimingViewProps) {
  const dirty = p.groups.some((g) => g.rows.some((r) => p.draft[r.key] !== undefined && p.draft[r.key] !== draftText(p.saved[r.key])))
  return (
    <Card title={TITLE} meta={SUMMARY} aria-label={TITLE}>
      <div className="settings-form">
        <p className="settings-note">{TIMING_NOTE}</p>
        {p.error ? <ErrorBanner error={p.error} onDismiss={p.onDismissError} describe={{ pcOnly: true }} /> : null}
        {p.groups.map((group) => (
          <div key={group.section} className="settings-group" role="group" aria-label={group.section}>
            <h4 className="settings-subhead">{group.section}</h4>
            {group.rows.map((row) => {
              const saved = p.saved[row.key]
              const text = p.draft[row.key] ?? draftText(saved)
              return (
                <div key={row.key} className="timing-row" data-testid={`timing-${row.key}`}>
                  <Field label={row.label} unit={row.unit || undefined} help={row.help || undefined} error={p.errors[row.key] ?? null}>
                    <input
                      type="text"
                      inputMode={row.type === 'int' ? 'numeric' : 'decimal'}
                      spellCheck={false}
                      value={text}
                      onChange={(e) => p.onChange(row.key, e.target.value)}
                    />
                  </Field>
                  <div className="timing-default">
                    <span className="muted" data-testid={`timing-default-${row.key}`}>{defaultLine(row)}</span>
                    <button
                      type="button"
                      className={buttonClass('ghost')}
                      aria-label={`Reset ${row.label} to default`}
                      disabled={p.busy || (isDefault(row, saved) && text === draftText(saved))}
                      onClick={() => p.onReset(row)}
                    >
                      Reset
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        ))}
        {!p.developerMode && <p className="settings-note">{DEV_ONLY_NOTE}</p>}
        <div className="settings-actions">
          <button type="button" className={buttonClass('primary')} disabled={p.busy || !dirty} onClick={p.onSave}>
            {p.busy ? 'Saving…' : 'Save'}
          </button>
          {dirty && !p.busy && (
            <button type="button" className={buttonClass('ghost')} onClick={p.onUndo}>
              Undo changes
            </button>
          )}
          <span className="muted" role="status">{p.note ?? ''}</span>
        </div>
      </div>
    </Card>
  )
}

export function TimingSettingsCard({ schema, settings, onSettings }: {
  schema: SettingsSchemaRow[] | null
  settings: SettingsOverview
  onSettings: (s: SettingsOverview) => void
}) {
  const developerMode = useDeveloperMode()
  const remote = usePcOnly() === 'remote'
  const groups = timingGroups(schema, developerMode)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)

  if (remote) {
    return (
      <Card title={TITLE} meta={PC_ONLY_SUMMARY} aria-label={TITLE}>
        <p className="muted">{PC_ONLY_BODY}</p>
      </Card>
    )
  }
  if (groups.length === 0) return null

  const saved: Record<string, number> = {}
  for (const g of groups) for (const r of g.rows) saved[r.key] = savedNumber(settings, r)

  const send = (patch: Partial<SettingsPreferences>, done: () => void) => {
    setBusy(true)
    setError(null)
    updatePreferences(patch).then(
      (s) => {
        setBusy(false)
        done()
        setNote('Saved.')
        onSettings(s)
      },
      (e: unknown) => {
        setBusy(false)
        setError(e)
      },
    )
  }

  const save = () => {
    const patch: Record<string, unknown> = {}
    const found: Record<string, string> = {}
    for (const g of groups) {
      for (const row of g.rows) {
        const text = draft[row.key]
        if (text === undefined || text === draftText(saved[row.key])) continue
        const parsed = toSavedValue(row, text)
        if (!parsed.ok) found[row.key] = parsed.error
        else if (parsed.value !== saved[row.key]) patch[row.key] = parsed.value
      }
    }
    setErrors(found)
    if (Object.keys(found).length) return
    if (Object.keys(patch).length === 0) {
      setDraft({})
      setNote('Nothing to change.')
      return
    }
    send(patch as Partial<SettingsPreferences>, () => setDraft({}))
  }

  const reset = (row: SettingsSchemaRow) =>
    send(resetPatch(row) as Partial<SettingsPreferences>, () => {
      setDraft((d) => Object.fromEntries(Object.entries(d).filter(([k]) => k !== row.key)))
      setErrors((e) => Object.fromEntries(Object.entries(e).filter(([k]) => k !== row.key)))
    })

  return (
    <TimingSettingsView
      groups={groups}
      saved={saved}
      draft={draft}
      errors={errors}
      busy={busy}
      note={note}
      developerMode={developerMode}
      error={error}
      onDismissError={() => setError(null)}
      onChange={(key, text) => {
        setDraft({ ...draft, [key]: text })
        setNote(null)
      }}
      onReset={reset}
      onSave={save}
      onUndo={() => {
        setDraft({})
        setErrors({})
      }}
    />
  )
}
