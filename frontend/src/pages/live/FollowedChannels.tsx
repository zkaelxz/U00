/*
 * Followed channels on the Live page: follow a YouTube channel (link or
 * @handle) and get a bell notification when it goes live or schedules a
 * stream. The server checks each channel about hourly (api/liveFollows.ts).
 * The PC owner may also let their own channels start Live automatically;
 * the mode picker offers that only when the list says this viewer may.
 * Start Live and "Use Live options" take the Live form's current options.
 */
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'

import {
  addFollow,
  announceLiveStarted,
  checkChannelInput,
  checkFollow,
  checkedText,
  describeFollowError,
  followStatusText,
  listFollows,
  optionsSummary,
  removeFollow,
  startFromFollow,
  updateFollow,
} from '../../api/liveFollows'
import { Badge } from '../../components/Badge'
import { Field } from '../../components/Field'
import { Section } from '../../components/Section'
import { buttonClass } from '../../components/uiClasses'
import type { BadgeTone } from '../../components/labels'
import type { LiveFollow, LiveFollowList, LiveFollowMode, LiveFollowOptions } from '../../types/live'

// After Check now, re-read the list this often until the check lands (or gives up).
const RECHECK_MS = 4000
const RECHECK_TRIES = 15

const STATE_TONE: Record<string, BadgeTone> = { live: 'accent', upcoming: 'info', not_live: 'neutral', error: 'warn' }
const MODE_LABEL: Record<LiveFollowMode, string> = { notify: 'Notify me', auto: 'Start Live automatically' }
const MODE_HELP =
  'Notify me: a bell notification when the channel goes live or schedules a stream. Start Live automatically (PC owner only): also starts Live with this channel’s saved options when nothing else is using the GPU and the monthly spending cap has room.'

type Props = {
  /** The Live form's current options, saved to a channel by "Use Live options". */
  liveOptions: LiveFollowOptions
}

export function FollowedChannels({ liveOptions }: Props) {
  const [list, setList] = useState<LiveFollowList | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [mode, setMode] = useState<LiveFollowMode>('notify')
  // What is running: 'add', or `${action}:${id}`. One request at a time.
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

  const waitForCheck = async (id: number, before: number | null) => {
    for (let i = 0; i < RECHECK_TRIES && alive.current; i++) {
      await new Promise((r) => setTimeout(r, RECHECK_MS))
      const r = await load()
      const row = r?.items.find((f) => f.id === id)
      if (!row || row.last_checked_at !== before) return
    }
  }

  const blocker = checkChannelInput(text)
  const items = list?.items ?? []
  const atLimit = !!list && items.filter((f) => f.mine).length >= list.max_per_member && !list.can_auto_start
  const addReason = busy ? 'Wait for the current request to finish.' : atLimit ? `You follow ${list?.max_per_member} channels, the most allowed. Remove one first.` : blocker
  const liveCount = items.filter((f) => f.live_now).length
  const summary = items.length
    ? `${items.length} channel${items.length === 1 ? '' : 's'}${liveCount ? ` · ${liveCount} live now` : ''}`
    : 'None yet'

  const add = async (e: FormEvent) => {
    e.preventDefault()
    if (addReason) return
    // Options go with an automatic start only: Notify me needs none until Start Live.
    const ok = await run('add', () =>
      addFollow({ channel: text.trim(), mode, ...(mode === 'auto' ? { options: liveOptions } : {}) }))
    if (ok) {
      setText('')
      setNote('Following. The first check runs in a moment.')
      const r = await load()
      const row = r?.items.find((f) => f.last_checked_at === null)
      if (row) void waitForCheck(row.id, null)
    }
  }

  return (
    <Section storageKey="live.follows" title="Followed channels" summary={summary} count={items.length || undefined}>
      <div className="live-follows">
        <form className="live-follow-add" onSubmit={(e) => void add(e)}>
          <Field label="Channel" help="A YouTube channel link (https://www.youtube.com/@name, /channel/UC… or /c/name) or an @handle. Baihe checks it about once an hour and tells you when it goes live or schedules a stream.">
            <input
              type="text"
              value={text}
              maxLength={300}
              placeholder="@channel or https://www.youtube.com/@channel"
              onChange={(e) => setText(e.target.value)}
              disabled={busy === 'add'}
            />
          </Field>
          {list?.can_auto_start && (
            <Field label="When live" help={MODE_HELP}>
              <select value={mode} onChange={(e) => setMode(e.target.value as LiveFollowMode)} disabled={busy === 'add'}>
                <option value="notify">{MODE_LABEL.notify}</option>
                <option value="auto">{MODE_LABEL.auto}</option>
              </select>
            </Field>
          )}
          <div className="actions">
            <button type="submit" className={buttonClass('secondary')} disabled={!!addReason}
              title={addReason ?? 'Follow this channel for live streams.'}>
              {busy === 'add' ? 'Following…' : 'Follow'}
            </button>
          </div>
          {text.trim() && addReason && <p className="muted">{addReason}</p>}
          {mode === 'auto' && list?.can_auto_start && (
            <p className="muted">Starts with the Live form’s current options: {optionsSummary(liveOptions)}.</p>
          )}
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
                onMode={(m) => void run(`mode:${f.id}`, async () => { await updateFollow(f.id, { mode: m, ...(m === 'auto' ? { options: liveOptions } : {}) }); await load() })}
                onOptions={() => void run(`options:${f.id}`, async () => { await updateFollow(f.id, { options: liveOptions }); await load(); setNote('Saved the Live options for this channel.') })}
                onCheck={() => void run(`check:${f.id}`, async () => { await checkFollow(f.id); setNote('Checking… the status updates in a moment.'); void waitForCheck(f.id, f.last_checked_at) })}
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
  onMode: (m: LiveFollowMode) => void
  onOptions: () => void
  onCheck: () => void
  onStart: () => void
  onRemove: () => void
}

function FollowRow({ f, busy, canAuto, onMode, onOptions, onCheck, onStart, onRemove }: RowProps) {
  const name = f.channel_name || f.channel
  const waiting = busy ? 'Wait for the current request to finish.' : null
  const label = (action: string, idle: string, working: string) => (busy === `${action}:${f.id}` ? working : idle)
  return (
    <li className="live-follow-row" data-testid="live-follow-row">
      <div className="live-follow-head">
        <span className="live-follow-name">{name}</span>
        {name !== f.channel && <span className="muted live-follow-label">{f.channel}</span>}
        {f.state && <Badge tone={STATE_TONE[f.state] ?? 'neutral'}>{f.state === 'live' ? 'Live' : f.state === 'upcoming' ? 'Scheduled' : f.state === 'error' ? 'Problem' : 'Not live'}</Badge>}
        {!f.mine && <span className="muted">another account’s</span>}
      </div>
      <p className="muted live-follow-status" data-testid="live-follow-status">
        {followStatusText(f)} · {checkedText(f.last_checked_at)}
      </p>
      <div className="live-follow-controls">
        {canAuto && f.mine ? (
          <select aria-label={`When ${name} goes live`} title={MODE_HELP} value={f.mode} disabled={!!busy}
            onChange={(e) => onMode(e.target.value as LiveFollowMode)}>
            <option value="notify">{MODE_LABEL.notify}</option>
            <option value="auto">{MODE_LABEL.auto}</option>
          </select>
        ) : (
          <span className="muted" title={MODE_HELP}>{MODE_LABEL[f.mode]}</span>
        )}
        {f.live_now && (
          <button type="button" className={buttonClass('primary', 'sm')} disabled={!!busy}
            title={waiting ?? `Start Live on this stream with this channel’s options (${optionsSummary(f.options)}).`}
            onClick={onStart}>
            {label('start', 'Start Live', 'Starting…')}
          </button>
        )}
        <button type="button" className={buttonClass('ghost', 'sm')} disabled={!!busy}
          title={waiting ?? 'Check this channel now instead of waiting for the hourly check.'} onClick={onCheck}>
          {label('check', 'Check now', 'Checking…')}
        </button>
        <button type="button" className={buttonClass('ghost', 'sm')} disabled={!!busy}
          title={waiting ?? `Save the Live form’s current options to this channel, for Start Live${canAuto ? ' and the automatic start' : ''}. Now: ${optionsSummary(f.options)}.`}
          onClick={onOptions}>
          {label('options', 'Use Live options', 'Saving…')}
        </button>
        <button type="button" className={buttonClass('danger', 'sm')} disabled={!!busy}
          title={waiting ?? 'Stop following this channel.'} onClick={onRemove}>
          {label('remove', 'Remove', 'Removing…')}
        </button>
      </div>
      {busy && busy.endsWith(`:${f.id}`) && <p className="muted live-follow-busy">Working…</p>}
    </li>
  )
}
