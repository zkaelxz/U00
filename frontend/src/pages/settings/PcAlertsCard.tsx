/*
 * Settings > Connections > PC alerts: when this PC's GPU, RAM, CPU or disk is
 * past a limit for a number of minutes, Baihe adds an Alerts entry and, if
 * Discord or ntfy is set up, pushes it. One row per alert: switch, limit and
 * minutes. A saved change applies on the monitor's next 30-second check; the
 * running timers restart when the app restarts. PC only.
 */
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'

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

  // The handlers read the latest rules and drafts from refs: a blur can follow a keystroke before React re-renders,
  // and a save's answer must be merged into the drafts as they are when it lands, not as they were when it was sent.
  const rulesRef = useRef<ResourceAlertRule[]>([])
  const draftsRef = useRef<Record<string, AlertDraft>>({})
  const inFlight = useRef(false)
  const saveAgain = useRef(false)
  const putDrafts = (next: Record<string, AlertDraft>) => {
    draftsRef.current = next
    setDrafts(next)
  }

  const load = (next: ResourceAlertRule[]) => {
    rulesRef.current = next
    setRules(next)
    putDrafts(Object.fromEntries(next.map((r) => [r.id, draftOf(r)])))
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
  // A row with a bad value keeps it as typed and sends only its switch, so one typo never blocks the others and a
  // rule can still be turned off. One save runs at a time; a change made meanwhile goes in the next one.
  const save = () => {
    if (inFlight.current) {
      saveAgain.current = true
      return
    }
    const sent = draftsRef.current
    const changes: Record<string, ResourceAlertChange> = {}
    for (const r of rulesRef.current) {
      const d = sent[r.id]
      if (!d) continue
      const c = hasProblem(r, d) ? (d.enabled !== r.enabled ? { enabled: d.enabled } : null) : changeOf(r, d)
      if (c) changes[r.id] = c
    }
    if (Object.keys(changes).length === 0) return
    inFlight.current = true
    setSaving(true)
    setError(null)
    setResourceAlerts(changes)
      .then(
        (r) => {
          rulesRef.current = r.rules
          setRules(r.rules)
          // Only a saved row that wasn't edited while the save ran takes the saved values; every other row keeps its draft.
          const cur = draftsRef.current
          putDrafts(Object.fromEntries(r.rules.map((rule) => [
            rule.id,
            rule.id in changes && cur[rule.id] === sent[rule.id] ? draftOf(rule) : (cur[rule.id] ?? draftOf(rule)),
          ])))
          setNote('Saved. It applies on the next check, within 30 seconds.')
        },
        (e: unknown) => setError(e),
      )
      .finally(() => {
        inFlight.current = false
        setSaving(false)
        if (saveAgain.current) {
          saveAgain.current = false
          save()
        }
      })
  }

  const edit = (id: string, patch: Partial<AlertDraft>, saveNow: boolean) => {
    setNote('')
    putDrafts({ ...draftsRef.current, [id]: { ...draftsRef.current[id], ...patch } })
    if (saveNow) save()
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
                <div key={r.id} className="alert-rule" role="group" aria-label={r.label}>
                  <Field label={r.label} help={ruleSummary(r)}>
                    <Toggle checked={d.enabled} disabled={saving} onChange={(next) => edit(r.id, { enabled: next }, true)} />
                  </Field>
                  {d.enabled && (
                    <AlertLimits
                      rule={r}
                      draft={d}
                      onChange={(patch) => edit(r.id, patch, false)}
                      onLeave={save}
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
  const errorId = `alert-limits-error-${r.id}`
  // Enter saves too, as it would submit a form.
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => { if (e.key === 'Enter') onLeave() }
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
            aria-describedby={limitError ? errorId : undefined}
            min={r.limit_min ?? undefined}
            max={r.limit_max ?? undefined}
            value={d.limit}
            onChange={(e) => onChange({ limit: e.target.value })}
            onBlur={onLeave}
            onKeyDown={onKeyDown}
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
        aria-describedby={minutesError ? errorId : undefined}
        min={r.minutes_min}
        max={r.minutes_max}
        value={d.minutes}
        onChange={(e) => onChange({ minutes: e.target.value })}
        onBlur={onLeave}
        onKeyDown={onKeyDown}
      />
      <span>min</span>
      {(limitError ?? minutesError) && <span id={errorId} className="field-error error" role="alert">{limitError ?? minutesError}</span>}
    </div>
  )
}
