/*
 * The Queue card on the Jobs page: titles and steps lined up to run one at a
 * time, now or once at a set time. The server owns the queue and starts each
 * item through the stage's own start path; this card only edits the list and
 * the run controls. A caller without jobs.start (403) or an older server
 * (404) sees no card.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

import { hasStatus } from '../../api/client'
import {
  addQueueAllSteps, addQueueItem, clearFinishedQueueItems, getQueue, moveQueueItem, pauseQueue, removeQueueItem, retryQueueItem,
  getQueueStepSettings, setQueuePolicy, setQueueSchedule, startQueue,
} from '../../api/queue'
import { getTranslateConfig } from '../../api/translateStage'
import { Badge } from '../../components/Badge'
import { Card } from '../../components/Card'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Section } from '../../components/Section'
import { Toggle } from '../../components/Toggle'
import { humanize } from '../../components/labels'
import { ButtonLink } from '../../components/Button'
import { buttonClass } from '../../components/uiClasses'
import { routeHref } from '../../router'
import type {
  QueueItem, QueueSettingEdits, QueueSettingField, QueueSettingValue, QueueState, QueueStep, QueueStepSettings,
} from '../../types/queue'
import {
  ADD_STEP_LABELS, HHMM, STEP_HELP, changedEdits, STEP_LABELS, canRemove, canRetry, finishedCount, itemName, runStateText, scheduleText,
  type AddStep, savedSettingValue, settingsStage, queueStatusLabel, statusTone, translateCostNote,
} from './queueModel'
import { useInterval } from '../../hooks/useInterval'
import { usePersistedState } from '../../hooks/usePersistedState'

// Fast while something runs, so a finished item shows up without a reload.
const ACTIVE_POLL_MS = 5000
const IDLE_POLL_MS = 30000
const SEARCHABLE_TITLES = 30

type Props = { titles: ReadonlyMap<number, string> }

export function JobQueue({ titles }: Props) {
  const [queue, setQueue] = useState<QueueState | null>(null)
  const [hidden, setHidden] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [dramaId, setDramaId] = useState('')
  const [titleFilter, setTitleFilter] = useState('')
  const [step, setStep] = useState<AddStep>('transcribe')
  const [costNote, setCostNote] = useState<string | null>(null)
  // This queued step's own changes to the title's saved settings; the title itself is never changed.
  const [edits, setEdits] = useState<QueueSettingEdits>({})
  const [fields, setFields] = useState<QueueSettingField[]>([])
  const [time, setTime] = useState('')
  const refocus = useRef<string | null>(null)
  const [folded, setFolded] = usePersistedState<boolean>('jobs.queueFolded.v1', false)

  // Kept apart from `error` so the next good read clears it without hiding a refused action's reason.
  const [loadError, setLoadError] = useState<unknown>(null)
  const load = useCallback(() => {
    getQueue().then(
      (q) => { setQueue(q); setHidden(false); setLoadError(null) },
      (e) => {
        if (hasStatus(e, 403, 404)) setHidden(true)
        else setLoadError(e)
      },
    )
  }, [])

  useEffect(load, [load])
  const active = queue?.state === 'running' || !!queue?.items.some((i) => i.status === 'running')
  useInterval(load, active ? ACTIVE_POLL_MS : IDLE_POLL_MS, { pauseWhenHidden: true })
  // The poll skips hidden ticks, so catch up at once on return instead of showing a stale queue until the next one.
  useEffect(() => {
    const onVisible = () => { if (!document.hidden) load() }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [load])

  // Before adding a Translate item, say which engine it will use and whether it costs money.
  useEffect(() => {
    setCostNote(null)
    const id = Number(dramaId)
    if ((step !== 'translate' && step !== 'all') || !id) return
    let live = true
    getTranslateConfig(id).then(
      (c) => {
        if (!live) return
        const engine = c.engines.find((e) => e.name === c.translation_engine)
        const label = humanize('engine', c.translation_engine)
        setCostNote(translateCostNote(label, !(engine?.free ?? false), c.monthly_cap_usd ?? 0))
      },
      () => undefined,
    )
    return () => { live = false }
  }, [dramaId, step])

  // Keyboard reordering: the moved item's button keeps focus.
  useLayoutEffect(() => {
    const key = refocus.current
    if (!key) return
    refocus.current = null
    const el = document.querySelector<HTMLButtonElement>(`[data-queue-focus="${key}"]`)
    if (el && !el.disabled) el.focus()
    else document.querySelector<HTMLButtonElement>(`[data-queue-focus^="${key.split(':')[0]}:"]:not(:disabled)`)?.focus()
  }, [queue])

  async function act(action: () => Promise<QueueState>) {
    setBusy(true)
    try {
      setQueue(await action())
      setError(null)
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  if (hidden) return null
  if (queue === null) return loadError ? <ErrorBanner error={loadError} /> : null
  const items = queue.items
  // A running queue stays open so its progress is never hidden; the saved choice returns once it stops.
  const open = active || !folded
  const full = items.length >= queue.max_items
  const sched = scheduleText(queue.schedule)
  const done = finishedCount(items)
  const sortedTitles = [...titles.entries()].sort((a, b) => a[1].localeCompare(b[1]))
  const needle = titleFilter.trim().toLowerCase()
  // The picked title stays listed so narrowing the filter never silently changes the selection.
  const titleOptions = needle
    ? sortedTitles.filter(([id, name]) => String(id) === dramaId || name.toLowerCase().includes(needle))
    : sortedTitles

  const add = () => {
    const id = Number(dramaId)
    if (!id) return
    const request = step === 'all'
      ? () => addQueueAllSteps(id)
      : () => addQueueItem(id, step, changedEdits(fields, edits))
    void act(request).then(() => setEdits({}))
  }
  const move = (item: QueueItem, dir: 'up' | 'down') => {
    refocus.current = `${item.id}:${dir}`
    void act(() => moveQueueItem(item.id, dir))
  }

  return (
    <Card
      title={
        <button type="button" className="queue-fold" aria-expanded={open} aria-disabled={active || undefined}
          onClick={() => { if (!active) setFolded(!folded) }}>
          <span aria-hidden="true">{open ? '▾' : '▸'}</span> Queue
        </button>
      }
      meta={runStateText(queue)} className="queue-card" aria-label="Queue"
    >
      {!open && <><ErrorBanner error={loadError} /><ErrorBanner error={error} onDismiss={() => setError(null)} /></>}
      {open && <>
      <Section title="How the queue works">
        <p className="muted">Line up steps for your titles and let them run one at a time, now or once at a set time.</p>
        {queue.can_control && (
          <>
            <p className="muted">Start at: once, at this time on the PC's clock. If Baihe is off then, the start shows as missed and nothing runs on its own.</p>
            <p className="muted">Stop the queue when an item fails: off, a failed item is marked and the next one starts. Skipped and cancelled items never stop the queue.</p>
          </>
        )}
      </Section>
      <ErrorBanner error={loadError} />
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {queue.notice && <p className="queue-notice" role="status">{queue.notice}</p>}

      {queue.schedule?.missed && queue.can_control ? (
        <div className="queue-missed" role="status" data-testid="queue-missed">
          <span>{sched}</span>
          <div className="queue-row">
            <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy} onClick={() => void act(startQueue)}>
              Start now
            </button>
            <button type="button" className={buttonClass('ghost', 'sm')} disabled={busy} onClick={() => void act(() => setQueueSchedule(null))}>
              Dismiss
            </button>
          </div>
        </div>
      ) : sched && <p className="muted" data-testid="queue-schedule">{sched}</p>}

      {items.length === 0 ? (
        <p className="muted" data-testid="queue-empty">No steps yet. Add one below.</p>
      ) : (
        <ol className="queue-items" aria-label="Queue items" data-testid="queue-items">
          {items.map((item, n) => (
            <QueueRow
              key={item.id}
              item={item}
              first={n === 0}
              last={n === items.length - 1}
              busy={busy}
              monthlyCap={queue.monthly_cap_usd}
              onMove={move}
              onRemove={() => void act(() => removeQueueItem(item.id))}
              onRetry={() => void act(() => retryQueueItem(item.id))}
            />
          ))}
        </ol>
      )}

      {titles.size > SEARCHABLE_TITLES && (
        <input type="search" aria-label="Filter titles" value={titleFilter} onChange={(e) => setTitleFilter(e.target.value)} />
      )}
      <div className="queue-add" role="group" aria-label="Add to queue">
        <Field label="Title">
          <select className="w-md" value={dramaId} onChange={(e) => { setDramaId(e.target.value); setEdits({}) }}>
            <option value="">Pick a title</option>
            {titleOptions.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select>
        </Field>
        <Field label="Step">
          <select className="w-md" value={step} onChange={(e) => { setStep(e.target.value as AddStep); setEdits({}) }}>
            {(Object.keys(ADD_STEP_LABELS) as AddStep[]).map((s) => <option key={s} value={s}>{ADD_STEP_LABELS[s]}</option>)}
          </select>
        </Field>
        <button type="button" className={buttonClass('secondary')} disabled={busy || !dramaId || full} onClick={add}>
          Add to queue
        </button>
      </div>
      <p className="muted queue-help" data-testid="queue-step-help">
        {STEP_HELP[step]}{(step === 'translate' || step === 'all') && costNote ? ` ${costNote}` : ''}
      </p>
      {Number(dramaId) > 0 && step !== 'all' && (
        <SavedSettings
          dramaId={Number(dramaId)}
          step={step}
          edits={edits}
          onFields={setFields}
          onEdit={(key, value) => setEdits((cur) => {
            const next = { ...cur }
            if (value === undefined) delete next[key]
            else next[key] = value
            return next
          })}
          onReset={() => setEdits({})}
        />
      )}
      {full && <p className="muted">The queue holds at most {queue.max_items} items. Clear finished items first.</p>}

      {queue.can_control ? (
        <div className="queue-controls" role="group" aria-label="Run the queue">
          <div className="queue-row">
            {queue.state === 'running' ? (
              <button type="button" className={buttonClass('secondary')} disabled={busy} onClick={() => void act(pauseQueue)}>
                Pause
              </button>
            ) : items.some((item) => item.status === 'waiting') && (
              <button type="button" className={buttonClass('primary')} disabled={busy} onClick={() => void act(startQueue)}>
                Start queue now
              </button>
            )}
          </div>
          <div className="queue-row">
            <Field label="Start at">
              <input type="time" value={time} onChange={(e) => setTime(e.target.value)} />
            </Field>
            <button
              type="button"
              className={buttonClass('secondary')}
              disabled={busy || !HHMM.test(time)}
              onClick={() => void act(() => setQueueSchedule(time))}
            >
              Set start time
            </button>
            {queue.schedule && !queue.schedule.missed && (
              <button type="button" className={buttonClass('ghost')} disabled={busy} onClick={() => void act(() => setQueueSchedule(null))}>
                Clear start time
              </button>
            )}
          </div>
          <div className="setting-list">
            <Field label="Stop the queue when an item fails">
              <Toggle checked={queue.stop_on_failure} disabled={busy} onChange={(on) => void act(() => setQueuePolicy(on))} />
            </Field>
          </div>
        </div>
      ) : (
        <p className="muted">Only the PC owner can start, pause or schedule the queue.</p>
      )}

      {done > 0 && (
        <div className="queue-row">
          <button type="button" className={buttonClass('ghost', 'sm')} disabled={busy} onClick={() => void act(clearFinishedQueueItems)}>
            Clear finished ({done})
          </button>
        </div>
      )}
      </>}
    </Card>
  )
}

type SavedProps = {
  dramaId: number
  step: QueueStep
  edits: QueueSettingEdits
  onFields: (fields: QueueSettingField[]) => void
  onEdit: (key: string, value: QueueSettingValue | undefined) => void
  onReset: () => void
}

/**
 * What the queued step will use for the chosen title, read from the server's own resolver. A Transcribe
 * step's settings can be changed here for that queued step only; the title's saved settings stay as they are.
 * The rows a Translate or Export step's controls replace are left out, so the cost line and the monthly cap stay readable.
 */
function SavedSettings({ dramaId, step, edits, onFields, onEdit, onReset }: SavedProps) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<QueueStepSettings | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    setData(null)
    setError(null)
    onFields([])
    if (!open) return
    let live = true
    getQueueStepSettings(dramaId, step).then(
      (d) => { if (live) { setData(d); onFields(d.fields ?? []) } },
      (e) => { if (live) setError(e) },
    )
    return () => { live = false }
  }, [open, dramaId, step, onFields])

  // A changed model or batch size re-prices the cost row; the controls stay as they are.
  const ownModel = typeof edits.model === 'string' ? edits.model : undefined
  const ownBatch = typeof edits.batch_size === 'number' && edits.batch_size >= 1 && edits.batch_size <= 60 ? edits.batch_size : undefined
  const loaded = data !== null
  const repriced = useRef(false)
  useEffect(() => {
    if (!loaded || step !== 'translate') return
    // The first read already priced the saved values.
    if (ownModel === undefined && ownBatch === undefined && !repriced.current) return
    repriced.current = ownModel !== undefined || ownBatch !== undefined
    let live = true
    getQueueStepSettings(dramaId, step, { model: ownModel, batchSize: ownBatch }).then(
      (d) => { if (live) setData((cur) => (cur ? { ...cur, rows: d.rows } : cur)) },
      () => undefined,
    )
    return () => { live = false }
  }, [loaded, dramaId, step, ownModel, ownBatch])

  const where = settingsStage(step)
  const fieldList = data?.fields ?? []
  const editable = fieldList.length > 0
  const controlLabels = new Set(fieldList.map((f) => f.label))
  // A Transcribe step shows its controls alone; the other steps keep the rows no control covers.
  const rows = !editable ? (data?.rows ?? []) : step === 'transcribe' ? [] : (data?.rows ?? []).filter((r) => !controlLabels.has(r.label))
  const changes = Object.keys(changedEdits(fieldList, edits) ?? {}).length
  return (
    <Section title="Saved settings for this step" onToggle={setOpen}>
      <ErrorBanner error={error} />
      {data && editable && (
        <div className="queue-settings-edit" data-testid="queue-edit-settings" role="group" aria-label="Settings for this queued step">
          <p className="muted">
            Starts at the title's saved settings. Changes here apply to this queued step only; the title keeps its own.
          </p>
          <div className="queue-settings-grid">
            {fieldList.map((f) => (
              <SettingControl key={f.key} field={f} value={f.key in edits ? edits[f.key] : f.value}
                onChange={(v) => onEdit(f.key, v === f.value ? undefined : v)} />
            ))}
          </div>
          {changes > 0 && (
            <div className="queue-row">
              <span className="muted" role="status">{changes === 1 ? '1 change' : `${changes} changes`} for this step</span>
              <button type="button" className={buttonClass('ghost', 'sm')} onClick={onReset}>Use saved settings</button>
            </div>
          )}
        </div>
      )}
      {data && rows.length > 0 && (
        <dl className="queue-settings" data-testid="queue-saved-settings">
          {rows.map((r) => (
            <div key={r.label} className="queue-setting"><dt>{r.label}</dt><dd>{savedSettingValue(r)}</dd></div>
          ))}
        </dl>
      )}
      <ButtonLink href={routeHref({ name: 'drama', id: dramaId, stage: where.stage })} size="sm">
        Change in the title's {where.label} stage
      </ButtonLink>
    </Section>
  )
}

function SettingControl({ field, value, onChange }: {
  field: QueueSettingField
  value: QueueSettingValue
  onChange: (v: QueueSettingValue) => void
}) {
  const help = field.locked ?? undefined
  if (field.kind === 'toggle') {
    return (
      <Field label={field.label} help={help}>
        <Toggle checked={value === true} disabled={!!field.locked} onChange={onChange} />
      </Field>
    )
  }
  if (field.kind === 'number') {
    return (
      <Field label={field.label} help={field.locked ?? (field.min !== null && field.max !== null ? `${field.min} to ${field.max}` : undefined)}>
        <input
          type="number" disabled={!!field.locked} inputMode="numeric" step={1} min={field.min ?? undefined} max={field.max ?? undefined}
          value={String(value)}
          onChange={(e) => { if (e.target.value !== '' && Number.isInteger(Number(e.target.value))) onChange(Number(e.target.value)) }}
        />
      </Field>
    )
  }
  return (
    <Field label={field.label} help={help}>
      <select value={String(value)} disabled={!!field.locked} onChange={(e) => onChange(e.target.value)}>
        {field.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </Field>
  )
}

type RowProps = {
  item: QueueItem
  first: boolean
  last: boolean
  busy: boolean
  monthlyCap: number
  onMove: (item: QueueItem, dir: 'up' | 'down') => void
  onRemove: () => void
  onRetry: () => void
}

export function QueueRow({ item, first, last, busy, monthlyCap, onMove, onRemove, onRetry }: RowProps) {
  const name = itemName(item)
  return (
    <li className="queue-item" data-testid={`queue-item-${item.id}`}>
      <div className="queue-item-head">
        <a href={routeHref({ name: 'drama', id: item.drama_id, stage: null })}>{item.title}</a>
        <span>{STEP_LABELS[item.step]}</span>
        <Badge tone={statusTone(item.status)}>{queueStatusLabel(item.status)}</Badge>
      </div>
      {item.reason && <p className={item.status === 'failed' ? 'error' : 'muted'}>{item.reason}</p>}
      {item.changed_settings.length > 0 && (
        <p className="muted" data-testid={`queue-item-changes-${item.id}`}>
          Changed for this step: {item.changed_settings.map((r) => `${r.label} ${r.value}`).join(', ')}
        </p>
      )}
      {item.step === 'translate' && item.engine && item.status === 'waiting' && (
        <p className="muted">{translateCostNote(humanize('engine', item.engine), item.paid, monthlyCap)}</p>
      )}
      {item.step === 'export' && item.status === 'done' && (
        <p><a href={`/api/artifacts/dramas/${item.drama_id}/subtitle`} download>Download subtitles</a></p>
      )}
      {item.mine && (
        <div className="queue-row queue-item-actions">
          <button type="button" className={buttonClass('secondary')} data-queue-focus={`${item.id}:up`}
            aria-label={`Move up ${name}`} disabled={busy || first} onClick={() => onMove(item, 'up')}>
            Move up
          </button>
          <button type="button" className={buttonClass('secondary')} data-queue-focus={`${item.id}:down`}
            aria-label={`Move down ${name}`} disabled={busy || last} onClick={() => onMove(item, 'down')}>
            Move down
          </button>
          {canRetry(item) && (
            <button type="button" className={buttonClass('secondary')} aria-label={`Retry ${name}`} disabled={busy} onClick={onRetry}>
              Retry
            </button>
          )}
          {canRemove(item) && (
            <ConfirmButton name={name} label="Remove…" verb="remove" confirmLabel="Confirm remove" busy={busy} onConfirm={onRemove} />
          )}
          {item.status === 'running' && <span className="muted">To stop it, cancel its job in the list below.</span>}
        </div>
      )}
    </li>
  )
}
