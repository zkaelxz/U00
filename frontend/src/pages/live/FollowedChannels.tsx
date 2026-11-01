/*
 * Followed channels on the Live page: follow a YouTube channel (link or
 * @handle) and check whether it is live. Checks are manual by default
 * (Check now, Check all my channels); each channel can also be checked
 * automatically every hour, which then sends one bell notification per
 * stream (api/liveFollows.ts). The PC owner may let their own channels start
 * Live automatically; that needs the hourly check, and the picker says so.
 * Start Live and "Use Live options" take the Live form's current options.
 */
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'

import {
  addFollow,
  announceLiveStarted,
  autoStartBlocker,
  checkAllFollows,
  checkChannelInput,
  checkFollow,
  checkedText,
  describeFollowError,
  followStatusText,
  listFollows,
  MAX_CHANNEL_INPUT,
  optionsSummary,
  removeFollow,
  startFromFollow,
  updateFollow,
} from '../../api/liveFollows'
import { Badge } from '../../components/Badge'
import { Field } from '../../components/Field'
import { Section } from '../../components/Section'
import { Toggle } from '../../components/Toggle'
import { ConfirmButton } from '../../components/ConfirmButton'
import { buttonClass } from '../../components/uiClasses'
import type { BadgeTone } from '../../components/labels'
import type { LiveFollow, LiveFollowList, LiveFollowMode, LiveFollowOptions, LiveFollowUpdate } from '../../types/live'

// While a check is queued or running, the list is re-read this often.
const CHECKING_POLL_MS = 3000

const STATE_TONE: Record<string, BadgeTone> = { live: 'accent', upcoming: 'info', not_live: 'neutral', error: 'warn' }
const STATE_LABEL: Record<string, string> = { live: 'Live', upcoming: 'Scheduled', not_live: 'Not live', error: 'Problem' }
const MODE_LABEL: Record<LiveFollowMode, string> = { notify: 'Notify me', auto: 'Start Live automatically' }
const MODE_HELP =
  'Notify me: a bell notification when the hourly check finds the channel live or a scheduled stream. Start Live automatically (PC owner only, needs Check every hour): also starts Live with this channel’s saved options when nothing else is using the GPU and the monthly spending cap has room.'
const HOURLY_HELP =
  'Off (the default): the channel is checked only when you press Check now or Check all. On: Baihe checks it about once an hour, plus once just after a stream scheduled within the hour starts, and sends one bell notification per stream.'

type Props = {
  /** The Live form's current options, saved to a channel by "Use Live options". */
  liveOptions: LiveFollowOptions
}

export function FollowedChannels({ liveOptions }: Props) {
  const [list, setList] = useState<LiveFollowList | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [text, setText] = useState('')
  // What is running: 'add', 'check-all', or `${action}:${id}`. One request at a time.
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const alive = useRef(true)

  const load = useCallback(async () => {
    try {
      const r = await listFollows()
      if (!alive.current) return null
      setList(r)
      setLoadError(null)
      return r
    } catch (err) {
      if (alive.current) setLoadError(describeFollowError(err))
      return null
    }
  }, [])

  useEffect(() => {
    alive.current = true
    void load()
    return () => {
      alive.current = false
    }
  }, [load])

  const items = list?.items ?? []
  const anyChecking = items.some((f) => f.checking)
  // Re-read while a check is queued or running, so its result shows in the row.
  useEffect(() => {
    if (!anyChecking) return
    const timer = window.setTimeout(() => void load(), CHECKING_POLL_MS)
    return () => window.clearTimeout(timer)
  }, [anyChecking, list, load])

  const run = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key)
    setError(null)
    setNote(null)
    try {
      await fn()
      return true
    } catch (err) {
      if (alive.current) setError(describeFollowError(err))
      return false
    } finally {
      if (alive.current) setBusy(null)
    }
  }

  const blocker = checkChannelInput(text)
  const atLimit = !!list && items.filter((f) => f.mine).length >= list.max_per_member && !list.can_auto_start
  const waiting = busy ? 'Wait for the current request to finish.' : null
  const addReason = waiting ?? (atLimit ? `You follow ${list?.max_per_member} channels, the most allowed. Remove one first.` : blocker)
  const changeable = items.filter((f) => f.mine || list?.can_auto_start)
  const checkAllReason = waiting ?? (changeable.length === 0 ? 'Follow a channel first.' : anyChecking ? 'Checking… wait for the current checks to finish.' : null)
  const addReasonShown = !!text.trim() && !!addReason
  // The same "wait" line would otherwise print twice.
  const checkAllReasonShown = !!checkAllReason && !(addReasonShown && checkAllReason === addReason)
  const liveCount = items.filter((f) => f.live_now).length
  const summary = items.length
    ? `${items.length} channel${items.length === 1 ? '' : 's'}${liveCount ? ` · ${liveCount} live now` : ''}`
    : 'None yet'

  const add = async (e: FormEvent) => {
    e.preventDefault()
    if (addReason) return
    const ok = await run('add', () => addFollow({ channel: text.trim() }))
    if (ok) {
      setText('')
      setNote('Following. Press Check now to see whether it is live.')
      await load()
    }
  }

  const change = (f: LiveFollow, key: string, body: LiveFollowUpdate, done?: string) =>
    void run(`${key}:${f.id}`, async () => {
      await updateFollow(f.id, body)
      await load()
      if (done) setNote(done)
    })

  return (
    <Section storageKey="live.follows" title="Followed channels" summary={summary} count={items.length || undefined}>
      <div className="live-follows">
        <form className="live-follow-add" onSubmit={(e) => void add(e)}>
          <Field label="Channel" help="A YouTube channel link (https://www.youtube.com/@name, /channel/UC… or /c/name), a Twitch channel link (https://www.twitch.tv/name) or an @handle. Press Check now to see whether it is live; turn on Check every hour to be told when it goes live.">
            <input
              type="text"
              value={text}
              maxLength={MAX_CHANNEL_INPUT}
              placeholder="@channel or https://www.youtube.com/@channel"
              onChange={(e) => setText(e.target.value)}
              disabled={busy === 'add'}
            />
          </Field>
          <div className="actions">
            <button type="submit" className={buttonClass('secondary')} disabled={!!addReason}
              aria-describedby={addReasonShown ? 'live-follow-add-reason' : undefined}
              title={addReasonShown ? undefined : addReason ?? 'Follow this channel for live streams.'}>
              {busy === 'add' ? 'Following…' : 'Follow'}
            </button>
            <button type="button" className={buttonClass('ghost')} disabled={!!checkAllReason}
              aria-describedby={checkAllReasonShown ? 'live-follow-check-all-reason' : undefined}
              title={checkAllReason ? undefined : `Check ${list?.can_auto_start ? 'every followed channel' : 'each of your channels'} now, one after another.`}
              onClick={() => void run('check-all', async () => {
                const r = await checkAllFollows()
                setNote(r.queued ? `Checking ${r.queued} channel${r.queued === 1 ? '' : 's'}…` : 'Nothing to check: every channel was checked in the last minute.')
                await load()
              })}>
              {anyChecking || busy === 'check-all' ? 'Checking…' : list?.can_auto_start ? 'Check all channels' : 'Check all my channels'}
            </button>
          </div>
          {addReasonShown && <p className="muted" id="live-follow-add-reason">{addReason}</p>}
          {checkAllReasonShown && <p className="muted" id="live-follow-check-all-reason">{checkAllReason}</p>}
        </form>
        {error && <p className="error" role="alert">{error}</p>}
        {note && <p className="muted" role="status">{note}</p>}
        {loadError && <p className="error" role="alert">{loadError}</p>}
        {list && !items.length && !loadError && (
          <p className="muted">No channels followed yet. Paste a channel link or an @handle above.</p>
        )}
        {items.length > 0 && (
          <ul className="live-follow-list" aria-label="Followed channels">
            {items.map((f) => (
              <FollowRow key={f.id} f={f} busy={busy} canAuto={!!list?.can_auto_start}
                onHourly={(on) => change(f, 'hourly', { auto_check: on })}
                onMode={(m) => change(f, 'mode', { mode: m, ...(m === 'auto' ? { options: liveOptions } : {}) })}
                onOptions={() => change(f, 'options', { options: liveOptions }, 'Saved the Live options for this channel.')}
                onCheck={() => void run(`check:${f.id}`, async () => { await checkFollow(f.id); await load() })}
                onStart={() => void run(`start:${f.id}`, async () => { const r = await startFromFollow(f.id); announceLiveStarted(r.session_id); setNote('Live started. The lines appear below.') })}
                onRemove={() => void run(`remove:${f.id}`, async () => { await removeFollow(f.id); await load() })}
              />
            ))}
          </ul>
        )}
      </div>
    </Section>
  )
}

type RowProps = {
  f: LiveFollow
  busy: string | null
  canAuto: boolean
  onHourly: (on: boolean) => void
  onMode: (m: LiveFollowMode) => void
  onOptions: () => void
  onCheck: () => void
  onStart: () => void
  onRemove: () => void
}

function FollowRow({ f, busy, canAuto, onHourly, onMode, onOptions, onCheck, onStart, onRemove }: RowProps) {
  const name = f.channel_name || f.channel
  const waiting = busy ? 'Wait for the current request to finish.' : null
  const label = (action: string, idle: string, working: string) => (busy === `${action}:${f.id}` ? working : idle)
  const editable = f.mine || canAuto
  const autoBlocked = autoStartBlocker(f)
  const checkReason = waiting ?? (f.checking ? 'Checking… the result shows here in a moment.' : null)
  const reasonId = `live-follow-reason-${f.id}`
  const autoNoteId = `live-follow-auto-${f.id}`
  return (
    <li className="live-follow-row" data-testid="live-follow-row">
      <div className="live-follow-head">
        <span className="live-follow-name">{name}</span>
        {name !== f.channel && <span className="muted live-follow-label">{f.channel}</span>}
        {f.state && <Badge tone={STATE_TONE[f.state] ?? 'neutral'}>{STATE_LABEL[f.state] ?? 'Not live'}</Badge>}
        {!f.mine && <span className="muted">another account’s</span>}
      </div>
      <p className="muted live-follow-status" data-testid="live-follow-status">
        {f.checking ? 'Checking…' : `${followStatusText(f)} · ${checkedText(f.last_checked_at)}`}
      </p>
      <div className="live-follow-controls">
        {editable && (
          <span className="live-follow-hourly" title={HOURLY_HELP}>
            <Toggle checked={f.auto_check} disabled={!!busy} aria-label={`Check ${name} every hour`} onChange={onHourly} />
            <span>Check every hour</span>
          </span>
        )}
        {canAuto && f.mine ? (
          <select aria-label={`When ${name} goes live`} aria-describedby={autoBlocked ? autoNoteId : undefined} title={MODE_HELP}
            value={f.mode} disabled={!!busy} onChange={(e) => onMode(e.target.value as LiveFollowMode)}>
            <option value="notify">{MODE_LABEL.notify}</option>
            <option value="auto" disabled={!!autoBlocked}>{MODE_LABEL.auto}{autoBlocked ? ' (needs Check every hour)' : ''}</option>
          </select>
        ) : (
          <span className="muted" title={MODE_HELP}>{MODE_LABEL[f.mode]}</span>
        )}
        {f.live_now && (
          <button type="button" className={buttonClass('primary', 'sm')} disabled={!!busy}
            aria-describedby={waiting ? reasonId : undefined}
            title={waiting ? undefined : `Start Live on this stream with this channel’s options (${optionsSummary(f.options)}).`}
            onClick={onStart}>
            {label('start', 'Start Live', 'Starting…')}
          </button>
        )}
        <button type="button" className={buttonClass('ghost', 'sm')} disabled={!!checkReason}
          aria-describedby={checkReason ? reasonId : undefined}
          title={checkReason ? undefined : 'Check this channel now. A channel can be checked once a minute.'} onClick={onCheck}>
          {f.checking ? 'Checking…' : label('check', 'Check now', 'Checking…')}
        </button>
        <button type="button" className={buttonClass('ghost', 'sm')} disabled={!!busy}
          aria-describedby={waiting ? reasonId : undefined}
          title={waiting ? undefined : `Save the Live form’s current options to this channel, for Start Live${canAuto ? ' and the automatic start' : ''}. Now: ${optionsSummary(f.options)}.`}
          onClick={onOptions}>
          {label('options', 'Use Live options', 'Saving…')}
        </button>
        <ConfirmButton name={name} label="Remove…" verb="remove" confirmLabel="Confirm remove" size="sm"
          busy={busy === `remove:${f.id}`} disabled={!!busy} onConfirm={onRemove} />
      </div>
      {checkReason && <p className="muted live-follow-note" id={reasonId}>{checkReason}</p>}
      {canAuto && f.mine && autoBlocked && (
        <p className="muted live-follow-note" id={autoNoteId}>Start Live automatically needs Check every hour on.</p>
      )}
    </li>
  )
}
