/*
 * Followed channels on the Live page: follow a YouTube channel (link or
 * @handle) and check whether it is live. Checks are manual by default
 * (Check now, Check all my channels); each channel can also be checked
 * automatically every hour, which then sends one bell notification per
 * stream (api/liveFollows.ts). The PC owner may let their own channels start
 * Live automatically; that needs the hourly check, and the picker says so.
 * Follow saves the Live form's current options to a new channel; Edit settings
 * changes a channel's own; Start Live uses the channel's saved ones.
 */
import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react'

import type { ApiError } from '../../api/client'
import {
  addFollow,
  autoStartBlocker,
  checkAllFollows,
  checkChannelsInput,
  checkFollow,
  channelPageUrl,
  checkedText,
  describeFollowError,
  followName,
  followOriginalName,
  followStatusText,
  MAX_CHANNEL_INPUT,
  MAX_CHANNELS_PER_ADD,
  optionsSummary,
  removeFollow,
  splitChannels,
  updateFollow,
} from '../../api/liveFollows'
import { Badge } from '../../components/Badge'
import { Field } from '../../components/Field'
import { HelpTip } from '../../components/HelpTip'
import { Section } from '../../components/Section'
import { Toggle } from '../../components/Toggle'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ExternalLink } from '../discover/ExternalLink'
import { buttonClass } from '../../components/uiClasses'
import type { BadgeTone } from '../../components/labels'
import type { LiveFollow, LiveFollowMode, LiveFollowOptions, LiveFollowUpdate, LiveFormOptions } from '../../types/live'
import type { TranslateEngine } from '../../types/translate'
import { FollowSettings } from './FollowSettings'
import type { Follows } from './useFollows'

const STATE_TONE: Record<string, BadgeTone> = { live: 'accent', upcoming: 'info', not_live: 'neutral', error: 'warn' }
const STATE_LABEL: Record<string, string> = { live: 'Live', upcoming: 'Scheduled', not_live: 'Not live', error: 'Problem' }
const MODE_LABEL: Record<LiveFollowMode, string> = { notify: 'Notify me', auto: 'Captions start automatically' }
const MODE_HELP =
  'Off: you get an alert when the channel goes live or schedules a stream, with a Watch button. On (PC owner only, needs Check every hour): captions also start by themselves with this channel’s settings when the GPU is free and the monthly cap has room.'
const HOURLY_HELP =
  'Off (default): checked only when you press Check now or Check all. On: checked about hourly, and once when a scheduled stream starts. One alert per stream.'

type Props = {
  follows: Follows
  /** A channel the bell's link picked: its row opens. */
  picked: number | null
  /** The Live form's current options, saved to a new channel. */
  liveOptions: LiveFollowOptions
  /** What the Live form offers (languages, Whisper models, ranges), for Edit settings. */
  formOptions: LiveFormOptions
  /** Engines with a key; null while they load. */
  engines: TranslateEngine[] | null
  /** Why Whisper can't run on the GPU here, or null. */
  gpuReason: string | null
}

export function FollowedChannels({ follows, picked, liveOptions, formOptions, engines, gpuReason }: Props) {
  const { list, loadError, load, alive, anyChecking } = follows
  const [text, setText] = useState('')
  // What is running: 'add', 'check-all', or `${action}:${id}`. One request at a time.
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  // The channel whose settings panel is open, if any.
  const [editing, setEditing] = useState<number | null>(null)
  // Rows the reader flipped from their default: closed, except the one a bell link picked, which starts open.
  const [flipped, setFlipped] = useState<Set<number>>(() => new Set())
  const pickedRow = useRef<HTMLLIElement | null>(null)
  useEffect(() => {
    if (picked && list) pickedRow.current?.scrollIntoView?.({ block: 'nearest' })
  }, [picked, list])
  const items = list?.items ?? []

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

  const blocker = checkChannelsInput(text)
  const waiting = busy ? 'Wait for the current request to finish.' : null
  // Until the engines load, the form's translator isn't known yet and Follow would save the wrong one.
  const addReason = waiting ?? blocker ?? (engines === null ? 'Loading engines…' : null)
  const changeable = items.filter((f) => f.mine || list?.can_auto_start)
  const checkAllReason = waiting ?? (changeable.length === 0 ? 'Follow a channel first.' : anyChecking ? 'Checking… wait for the current checks to finish.' : null)
  const addReasonShown = !!text.trim() && !!addReason
  // The same "wait" line would otherwise print twice.
  const emptyShown = !!list && !items.length && !loadError
  // On an empty list the "No channels followed yet" line already says it, so "Follow a channel first." would repeat it.
  const checkAllReasonShown = !!checkAllReason && !(addReasonShown && checkAllReason === addReason) && !(emptyShown && changeable.length === 0 && !waiting)
  const liveCount = items.filter((f) => f.live_now).length
  const summary = items.length
    ? `${items.length} channel${items.length === 1 ? '' : 's'}${liveCount ? ` · ${liveCount} live now` : ''}`
    : 'None yet'

  const add = async (e: FormEvent) => {
    e.preventDefault()
    if (addReason) return
    const failed: string[] = []
    const problems: string[] = []
    let followed = 0
    let withoutSettings = 0
    await run('add', async () => {
      const channels = splitChannels(text)
      for (const [i, channel] of channels.entries()) {
        try {
          try {
            await addFollow({ channel, options: liveOptions })
          } catch (err) {
            // The form's translator may be one this account can't use (a paid or cloud engine):
            // keep the other settings with the free default translator (Ollama on this PC).
            if ((err as Partial<ApiError> | null)?.status !== 403) throw err
            await addFollow({ channel, options: { ...liveOptions, engine: null, model: null } })
            withoutSettings += 1
          }
          followed += 1
        } catch (err) {
          failed.push(channel)
          if ((err as Partial<ApiError> | null)?.status === 429) {
            // A member's hourly add limit: the rest would all be refused the same way.
            failed.push(...channels.slice(i + 1))
            problems.push('Too many channels added in the last hour; try the rest later')
            break
          }
          problems.push(`${channel}: ${describeFollowError(err)}`)
        }
      }
    })
    if (!alive.current) return
    setText(failed.join('\n'))
    const parts: string[] = []
    if (followed) {
      parts.push(`Following ${followed === 1 ? 'it' : `${followed} channels`} with the Live form’s settings. Press Check now to see whether ${followed === 1 ? 'it is' : 'they are'} live.`)
    }
    if (withoutSettings) {
      parts.push(`${withoutSettings === 1 ? 'One was' : `${withoutSettings} were`} saved with Ollama on this PC instead of the form’s translator, which your account can’t use.`)
    }
    if (parts.length) setNote(parts.join(' '))
    if (problems.length) setError(`Not followed (still in the box): ${problems.join(' · ')}`)
    if (followed) await load()
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
        {list && <Section storageKey="live.followAdd" title="Follow a channel" defaultOpen={!items.length}>
        <form className="live-follow-add" onSubmit={(e) => void add(e)}>
          <Field label="Channels" help={`One per line, up to ${MAX_CHANNELS_PER_ADD}: a YouTube channel link (…/@name, /channel/UC… or /c/name), a Twitch link (twitch.tv/name) or an @handle. Each new channel keeps the Live form’s current settings (language, caption model, translator, GPU) for Start Live. Check now shows whether it is live; Check every hour alerts you when it goes live.`}>
            <textarea
              rows={2}
              value={text}
              maxLength={MAX_CHANNEL_INPUT * MAX_CHANNELS_PER_ADD}
              placeholder={'@channel or https://www.youtube.com/@channel\nOne per line to follow several'}
              onChange={(e) => setText(e.target.value)}
              disabled={busy === 'add'}
            />
          </Field>
          <div className="actions">
            <button type="submit" className={buttonClass('secondary')} disabled={!!addReason}
              aria-describedby={addReasonShown ? 'live-follow-add-reason' : undefined}
              title={addReasonShown ? undefined : addReason ?? 'Follow these channels for live streams, with the Live form’s current settings.'}>
              {busy === 'add' ? 'Following…' : 'Follow'}
            </button>
          </div>
          {addReasonShown && <p className="muted" id="live-follow-add-reason">{addReason}</p>}
        </form>
        </Section>}
        <div className="actions">
          <button type="button" className={buttonClass('ghost')} disabled={!!checkAllReason}
              aria-describedby={checkAllReasonShown ? 'live-follow-check-all-reason' : undefined}
              title={checkAllReason ? undefined : `Check ${list?.can_auto_start ? 'every followed channel' : 'each of your channels'} now, one after another.`}
              onClick={() => void run('check-all', async () => {
                const r = await checkAllFollows()
                setNote(r.queued ? `Checking ${r.queued} channel${r.queued === 1 ? '' : 's'}…` : 'All channels were just checked.')
                await load()
              })}>
              {anyChecking || busy === 'check-all' ? 'Checking…' : list?.can_auto_start ? 'Check all channels' : 'Check all my channels'}
            </button>
        </div>
          {checkAllReasonShown && <p className="muted" id="live-follow-check-all-reason">{checkAllReason}</p>}

        {error && <p className="error" role="alert">{error}</p>}
        {note && <p className="muted" role="status">{note}</p>}
        {loadError && <p className="error" role="alert">{loadError}</p>}
        {emptyShown && (
          <p className="muted">No channels followed yet. Paste a channel link or an @handle above.</p>
        )}
        {items.length > 0 && (
          <ul className="live-follow-list" aria-label="Followed channels">
            {items.map((f) => (
              <FollowRow key={f.id} f={f} busy={busy} canAuto={!!list?.can_auto_start}
                open={(f.id === picked) !== flipped.has(f.id)} picked={f.id === picked} rowRef={f.id === picked ? pickedRow : undefined}
                onToggle={() => setFlipped((s) => { const n = new Set(s); if (!n.delete(f.id)) n.add(f.id); return n })}
                onHourly={(on) => change(f, 'hourly', { auto_check: on })}
                onMode={(m) => change(f, 'mode', { mode: m })}
                editing={editing === f.id}
                onEdit={() => setEditing(editing === f.id ? null : f.id)}
                onNickname={(nickname) => change(f, 'nickname', { nickname }, nickname ? `Saved the name ${nickname}.` : 'Removed the name.')}
                settings={editing === f.id && (
                  <FollowSettings name={followName(f)} saved={f.options} fromForm={liveOptions}
                    opts={formOptions} engines={engines} gpuReason={gpuReason} busy={!!busy} saving={busy === `options:${f.id}`}
                    onCancel={() => setEditing(null)}
                    onSave={(options) => void run(`options:${f.id}`, async () => {
                      await updateFollow(f.id, { options })
                      setEditing(null)
                      await load()
                      setNote(`Saved the settings for ${followName(f)}.`)
                    })} />
                )}
                onCheck={() => void run(`check:${f.id}`, async () => { await checkFollow(f.id); await load() })}
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
  open: boolean
  picked: boolean
  rowRef?: React.Ref<HTMLLIElement>
  onToggle: () => void
  onHourly: (on: boolean) => void
  onMode: (m: LiveFollowMode) => void
  editing: boolean
  onEdit: () => void
  onNickname: (nickname: string) => void
  settings: ReactNode
  onCheck: () => void
  onRemove: () => void
}

/** A name of the account's own for a channel, for when the site's name is only characters it cannot read. */
function NicknameEditor({ f, disabled, onSave }: { f: LiveFollow; disabled: boolean; onSave: (nickname: string) => void }) {
  const [text, setText] = useState(f.nickname)
  const unchanged = text.trim() === f.nickname
  return (
    <form className="live-follow-nickname" onSubmit={(e) => { e.preventDefault(); if (!unchanged) onSave(text.trim()) }}>
      <Field label="Your name for it" help="A name only you see, shown first in your Live list and alerts. Leave it empty to show the channel's own name.">
        <input type="text" value={text} maxLength={60} placeholder={f.channel_name || f.channel} disabled={disabled} onChange={(e) => setText(e.target.value)} />
      </Field>
      <button type="submit" className={buttonClass('secondary', 'sm')} disabled={disabled || unchanged}>Save name</button>
    </form>
  )
}

function FollowRow({ f, busy, canAuto, open, picked, rowRef, onToggle, onHourly, onMode, editing, onEdit, onNickname, settings, onCheck, onRemove }: RowProps) {
  const name = followName(f)
  const original = followOriginalName(f)
  const waiting = busy ? 'Wait for the current request to finish.' : null
  const label = (action: string, idle: string, working: string) => (busy === `${action}:${f.id}` ? working : idle)
  const editable = f.mine || canAuto
  const autoBlocked = autoStartBlocker(f)
  const checkReason = waiting ?? (f.checking ? 'Checking… the result shows here in a moment.' : null)
  const reasonId = `live-follow-reason-${f.id}`
  const autoNoteId = `live-follow-auto-${f.id}`
  const bodyId = `live-follow-body-${f.id}`
  // In the phone app a link to another site opens in Grab, where a stream or video can be sent to Baihe.
  const pageUrl = channelPageUrl(f.channel)
  const [hourlyHelpId, modeHelpId] = [useId(), useId()]
  return (
    <li className="live-follow-row" data-testid="live-follow-row" ref={rowRef} data-picked={picked || undefined}>
      <button type="button" className="live-follow-toggle" aria-expanded={open} aria-controls={bodyId}
        aria-label={`${name}: ${open ? 'hide' : 'show'} details`} onClick={onToggle}>
        <span className="live-follow-name">{name}</span>
        {original && <small className="muted live-follow-original">{original}</small>}
        {f.state && <Badge tone={STATE_TONE[f.state] ?? 'neutral'}>{STATE_LABEL[f.state] ?? 'Not live'}</Badge>}
        <span className="live-follow-chevron" aria-hidden="true">{open ? '▾' : '▸'}</span>
      </button>
      <p className="muted live-follow-status" data-testid="live-follow-status">
        {f.checking ? 'Checking…' : `${followStatusText(f)} · ${checkedText(f.last_checked_at)}`}
      </p>
      <div id={bodyId} hidden={!open} className="live-follow-body">
        {name !== f.channel && <p className="muted live-follow-label">{f.channel}</p>}
        {editable && <NicknameEditor key={f.nickname} f={f} disabled={!!busy} onSave={onNickname} />}
        <p className="muted live-follow-settings">Start Live uses: {optionsSummary(f.options)}</p>
        <div className="live-follow-controls">
          {editable && (
            <span className="live-follow-hourly">
              <Toggle checked={f.auto_check} disabled={!!busy} aria-label={`Check ${name} every hour`} onChange={onHourly} />
              <span>Check every hour</span>
              <HelpTip label={`Check ${name} every hour`} id={hourlyHelpId}>{HOURLY_HELP}</HelpTip>
            </span>
          )}
          {canAuto && f.mine ? (
            <span className="live-follow-hourly">
              <Toggle checked={f.mode === 'auto'} disabled={!!busy || (f.mode !== 'auto' && !!autoBlocked)}
                aria-label={`Start captions automatically when ${name} goes live`}
                aria-describedby={autoBlocked ? autoNoteId : undefined}
                onChange={(on) => onMode(on ? 'auto' : 'notify')} />
              <span>Start captions automatically</span>
              <HelpTip label="Start captions automatically" id={modeHelpId}>{MODE_HELP}</HelpTip>
            </span>
          ) : (
            <span className="muted">{MODE_LABEL[f.mode]}<HelpTip label={MODE_LABEL[f.mode]} id={modeHelpId}>{MODE_HELP}</HelpTip></span>
          )}
          <button type="button" className={buttonClass('ghost', 'sm')} disabled={!!checkReason}
            aria-describedby={checkReason ? reasonId : undefined}
            title={checkReason ? undefined : 'At most once a minute.'} onClick={onCheck}>
            {f.checking ? 'Checking…' : label('check', 'Check now', 'Checking…')}
          </button>
          <button type="button" className={buttonClass('ghost', 'sm')} disabled={!!busy}
            aria-describedby={waiting ? reasonId : undefined}
            aria-expanded={editing}
            title={waiting ? undefined : 'Change the language, caption model, translator and GPU that Start Live uses for this channel.'}
            onClick={onEdit}>
            {editing ? 'Close settings' : 'Edit settings'}
          </button>
          {pageUrl && (
            <ExternalLink href={pageUrl} className={buttonClass('ghost', 'sm')}>
              Open channel page
            </ExternalLink>
          )}
          <ConfirmButton name={name} label="Remove…" verb="remove" confirmLabel="Confirm remove" size="sm"
            busy={busy === `remove:${f.id}`} disabled={!!busy} onConfirm={onRemove} />
        </div>
        {checkReason && <p className="muted live-follow-note" id={reasonId}>{checkReason}</p>}
        {canAuto && f.mine && autoBlocked && f.mode !== 'auto' && (
          <p className="muted live-follow-note" id={autoNoteId}>Captions can start by themselves only with Check every hour on.</p>
        )}
        {settings}
      </div>
    </li>
  )
}
