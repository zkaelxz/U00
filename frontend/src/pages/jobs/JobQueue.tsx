/*
 * The Queue card on the Jobs page: titles and steps lined up to run one at a
 * time, now or once at a set time. The server owns the queue and starts each
 * item through the stage's own start path; this card only edits the list and
 * the run controls. A caller without jobs.start (403) or an older server
 * (404) sees no card.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

import { ApiError } from '../../api/client'
import {
  addQueueItem, clearFinishedQueueItems, getQueue, moveQueueItem, pauseQueue, removeQueueItem, retryQueueItem,
  setQueuePolicy, setQueueSchedule, startQueue,
} from '../../api/queue'
import { getTranslateConfig } from '../../api/translateStage'
import { Badge } from '../../components/Badge'
import { Card } from '../../components/Card'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import { humanize } from '../../components/labels'
import { buttonClass } from '../../components/uiClasses'
import { routeHref } from '../../router'
import type { QueueItem, QueueState, QueueStep } from '../../types/queue'
import {
  HHMM, STEP_HELP, STEP_LABELS, canRemove, canRetry, finishedCount, itemName, runStateText, scheduleText,
  statusLabel, statusTone, translateCostNote,
} from './queueModel'

// Fast while something runs, so a finished item shows up without a reload.
const ACTIVE_POLL_MS = 5000
const IDLE_POLL_MS = 30000

type Props = { titles: ReadonlyMap<number, string> }

export function JobQueue({ titles }: Props) {
  const [queue, setQueue] = useState<QueueState | null>(null)
  const [hidden, setHidden] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [dramaId, setDramaId] = useState('')
  const [step, setStep] = useState<QueueStep>('transcribe')
  const [costNote, setCostNote] = useState<string | null>(null)
  const [time, setTime] = useState('')
  const refocus = useRef<string | null>(null)

  const load = useCallback(() => {
    getQueue().then(
      (q) => { setQueue(q); setHidden(false) },
      (e) => {
        if (e instanceof ApiError && (e.status === 403 || e.status === 404)) setHidden(true)
        else setError(e)
      },
    )
  }, [])

  useEffect(load, [load])
  const active = queue?.state === 'running' || !!queue?.items.some((i) => i.status === 'running')
  useEffect(() => {
    const t = window.setInterval(load, active ? ACTIVE_POLL_MS : IDLE_POLL_MS)
    return () => window.clearInterval(t)
  }, [load, active])

  // Before adding a Translate item, say which engine it will use and whether it costs money.
  useEffect(() => {
    setCostNote(null)
    const id = Number(dramaId)
    if (step !== 'translate' || !id) return
    let live = true
    getTranslateConfig(id).then(
      (c) => {
        if (!live) return
        const engine = c.engines.find((e) => e.name === c.translation_engine)
        const label = engine?.label ?? humanize('engine', c.translation_engine)
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

  if (hidden || queue === null) return null
  const items = queue.items
  const full = items.length >= queue.max_items
  const sched = scheduleText(queue.schedule)
  const done = finishedCount(items)
  const titleOptions = [...titles.entries()].sort((a, b) => a[1].localeCompare(b[1]))

  const add = () => {
    const id = Number(dramaId)
    if (id) void act(() => addQueueItem(id, step))
  }
  const move = (item: QueueItem, dir: 'up' | 'down') => {
    refocus.current = `${item.id}:${dir}`
    void act(() => moveQueueItem(item.id, dir))
  }

  return (
    <Card title="Queue" meta={runStateText(queue)} className="queue-card" aria-label="Queue">
      <p className="muted">Line up steps for your titles and let them run one at a time, now or once at a set time.</p>
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
        <p className="muted" data-testid="queue-empty">The queue is empty.</p>
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

      <div className="queue-add" role="group" aria-label="Add to queue">
        <Field label="Title">
          <select value={dramaId} onChange={(e) => setDramaId(e.target.value)}>
            <option value="">Pick a title</option>
            {titleOptions.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select>
        </Field>
        <Field label="Step">
          <select value={step} onChange={(e) => setStep(e.target.value as QueueStep)}>
            {(Object.keys(STEP_LABELS) as QueueStep[]).map((s) => <option key={s} value={s}>{STEP_LABELS[s]}</option>)}
          </select>
        </Field>
        <button type="button" className={buttonClass('secondary')} disabled={busy || !dramaId || full} onClick={add}>
          Add to queue
        </button>
      </div>
      <p className="muted queue-help" data-testid="queue-step-help">
        {STEP_HELP[step]}{step === 'translate' && costNote ? ` ${costNote}` : ''}
      </p>
      {full && <p className="muted">The queue holds at most {queue.max_items} items. Clear finished items first.</p>}

      {queue.can_control ? (
        <div className="queue-controls" role="group" aria-label="Run the queue">
          <div className="queue-row">
            {queue.state === 'running' ? (
              <button type="button" className={buttonClass('secondary')} disabled={busy} onClick={() => void act(pauseQueue)}>
                Pause
              </button>
            ) : (
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
          <p className="muted">Once, at this time on the PC's clock. If Baihe is off then, the start shows as missed and nothing runs on its own.</p>
          <div className="setting-list">
            <Field label="Stop the queue when an item fails">
              <Toggle checked={queue.stop_on_failure} disabled={busy} onChange={(on) => void act(() => setQueuePolicy(on))} />
            </Field>
          </div>
          <p className="muted">Off: a failed item is marked and the next one starts. Skipped and cancelled items never stop the queue.</p>
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
    </Card>
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
        <Badge tone={statusTone(item.status)}>{statusLabel(item.status)}</Badge>
      </div>
      {item.reason && <p className={item.status === 'failed' ? 'error' : 'muted'}>{item.reason}</p>}
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
            <button type="button" className={buttonClass('ghost')} aria-label={`Remove ${name}`} disabled={busy} onClick={onRemove}>
              Remove
            </button>
          )}
          {item.status === 'running' && <span className="muted">To stop it, cancel its job in the list below.</span>}
        </div>
      )}
    </li>
  )
}
