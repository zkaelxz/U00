/*
 * Settings > Connections > PC alerts: when this PC's GPU, RAM, CPU or disk is
 * past a limit for a number of minutes, Baihe adds an Alerts entry and, if
 * Discord or ntfy is set up, pushes it. One row per alert: switch, limit and
 * minutes. A saved change applies on the monitor's next 30-second check; the
 * running timers restart when the app restarts. PC only.
 */
import { useEffect, useState } from 'react'

import { getResourceAlerts, setResourceAlerts } from '../../api/resourceAlerts'
import { getPcMode, loadPcMode } from '../../api/pcOnly'
import { Card } from '../../components/Card'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import type { ResourceAlertChange, ResourceAlertRule } from '../../types/resourceAlerts'
import { type AlertDraft, changeOf, draftOf, hasProblem, limitProblem, minutesProblem, ruleSummary } from './pcAlerts'

const TITLE = 'PC alerts'

export function PcAlertsCard() {
  const [rules, setRules] = useState<ResourceAlertRule[] | null>(null)
  const [drafts, setDrafts] = useState<Record<string, AlertDraft>>({})
  const [error, setError] = useState<unknown>(null)
  const [saving, setSaving] = useState(false)
  const [note, setNote] = useState('')

  const load = (next: ResourceAlertRule[]) => {
    setRules(next)
    setDrafts(Object.fromEntries(next.map((r) => [r.id, draftOf(r)])))
  }

  // Wait for /api/meta first, so a viewer away from the PC makes no calls here.
  useEffect(() => {
    let live = true
    void loadPcMode().then(() => {
      if (!live || getPcMode() === 'remote') return
      getResourceAlerts().then(
        (r) => live && load(r.rules),
        (e: unknown) => live && setError(e),
      )
    })
    return () => {
      live = false
    }
  }, [])

  // Changes are saved as soon as a switch is flipped or a field is left, like the switches around this card.
  // Rows with a bad value are skipped until fixed, so one typo never blocks the others.
  const save = (nextDrafts: Record<string, AlertDraft>) => {
    const changes: Record<string, ResourceAlertChange> = {}
    for (const r of rules ?? []) {
      const d = nextDrafts[r.id]
      if (!d || hasProblem(r, d)) continue
      const c = changeOf(r, d)
      if (c) changes[r.id] = c
    }
    if (Object.keys(changes).length === 0) return
    setSaving(true)
    setError(null)
    setResourceAlerts(changes).then(
      (r) => {
        load(r.rules)
        setNote('Saved. It applies on the next check, within 30 seconds.')
        setSaving(false)
      },
      (e: unknown) => {
        setError(e)
        setSaving(false)
      },
    )
  }

  const edit = (id: string, patch: Partial<AlertDraft>, saveNow: boolean) => {
    setNote('')
    const next = { ...drafts, [id]: { ...drafts[id], ...patch } }
    setDrafts(next)
    if (saveNow) save(next)
  }

  return (
    <Card title={TITLE} aria-label={TITLE}>
      <p className="settings-note">
        Baihe alerts you when this PC stays past a limit for the minutes shown, once an hour while it lasts, and again
        when it is back to normal. Alerts show in the bell and go to Discord or ntfy if one is set up.
      </p>
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ pcOnly: true }} />
      {!rules ? (
        !error && <p className="muted">Loading…</p>
      ) : (
        <>
          <div className="setting-list">
            {rules.map((r) => {
              const d = drafts[r.id]
              if (!d) return null
              return (
                <div key={r.id} className="setting-list" role="group" aria-label={r.label}>
                  <Field label={r.label} help={ruleSummary(r)}>
                    <Toggle checked={d.enabled} disabled={saving} onChange={(next) => edit(r.id, { enabled: next }, true)} />
                  </Field>
                  {d.enabled && (
                    <AlertLimits
                      rule={r}
                      draft={d}
                      onChange={(patch) => edit(r.id, patch, false)}
                      onLeave={() => save(drafts)}
                    />
                  )}
                </div>
              )
            })}
          </div>
          <p className="muted" role="status">
            {note}
          </p>
          <p className="settings-note">
            Timers start over when Baihe restarts. GPU memory (full) and CPU use are off by default.
          </p>
        </>
      )}
    </Card>
  )
}

// "Alert at [85] % for [15] min" on one line; hidden while the rule is off.
function AlertLimits({ rule: r, draft: d, onChange, onLeave }: {
  rule: ResourceAlertRule
  draft: AlertDraft
  onChange: (patch: Partial<AlertDraft>) => void
  onLeave: () => void
}) {
  const limitError = limitProblem(r, d.limit)
  const minutesError = minutesProblem(r, d.minutes)
  return (
    <div className="alert-limits">
      {r.has_limit && (
        <>
          <span>Alert {r.below ? 'below' : 'at'}</span>
          <input
            type="number"
            inputMode="decimal"
            aria-label={`${r.label}: alert ${r.below ? 'below' : 'at or above'} (${r.unit})`}
            aria-invalid={limitError != null}
            min={r.limit_min ?? undefined}
            max={r.limit_max ?? undefined}
            value={d.limit}
            onChange={(e) => onChange({ limit: e.target.value })}
            onBlur={onLeave}
          />
          <span>{r.unit} for</span>
        </>
      )}
      {!r.has_limit && <span>Alert after</span>}
      <input
        type="number"
        inputMode="numeric"
        aria-label={`${r.label}: after (minutes)`}
        aria-invalid={minutesError != null}
        min={r.minutes_min}
        max={r.minutes_max}
        value={d.minutes}
        onChange={(e) => onChange({ minutes: e.target.value })}
        onBlur={onLeave}
      />
      <span>min</span>
      {(limitError ?? minutesError) && <span className="field-error" role="alert">{limitError ?? minutesError}</span>}
    </div>
  )
}
