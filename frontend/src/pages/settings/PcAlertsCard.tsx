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
import { buttonClass } from '../../components/uiClasses'
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

  const edit = (id: string, patch: Partial<AlertDraft>) => {
    setNote('')
    setDrafts((cur) => ({ ...cur, [id]: { ...cur[id], ...patch } }))
  }

  const changes: Record<string, ResourceAlertChange> = {}
  let invalid = false
  for (const r of rules ?? []) {
    const d = drafts[r.id]
    if (!d) continue
    if (hasProblem(r, d)) invalid = true
    else {
      const c = changeOf(r, d)
      if (c) changes[r.id] = c
    }
  }
  const dirty = Object.keys(changes).length > 0

  const save = () => {
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
                    <Toggle checked={d.enabled} disabled={saving} onChange={(next) => edit(r.id, { enabled: next })} />
                  </Field>
                  {r.has_limit && (
                    <Field
                      label={`${r.label}: ${r.below ? 'alert below' : 'alert at or above'} (${r.unit})`}
                      error={limitProblem(r, d.limit)}
                    >
                      <input
                        type="number"
                        inputMode="decimal"
                        min={r.limit_min ?? undefined}
                        max={r.limit_max ?? undefined}
                        value={d.limit}
                        disabled={saving}
                        onChange={(e) => edit(r.id, { limit: e.target.value })}
                      />
                    </Field>
                  )}
                  <Field label={`${r.label}: after (minutes)`} error={minutesProblem(r, d.minutes)}>
                    <input
                      type="number"
                      inputMode="numeric"
                      min={r.minutes_min}
                      max={r.minutes_max}
                      value={d.minutes}
                      disabled={saving}
                      onChange={(e) => edit(r.id, { minutes: e.target.value })}
                    />
                  </Field>
                </div>
              )
            })}
          </div>
          <div className="settings-actions">
            <button
              type="button"
              className={buttonClass('primary')}
              disabled={saving || invalid || !dirty}
              onClick={save}
            >
              {saving ? 'Saving…' : 'Save PC alerts'}
            </button>
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
