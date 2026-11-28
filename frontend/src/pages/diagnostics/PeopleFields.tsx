/** The fields inside an opened People row (name, paid limit, jobs a day), each saving itself without a Save button. */
import { useRef, useState } from 'react'

import { describeError } from '../../components/errorMessages'
import { HelpTip } from '../../components/HelpTip'
import type { AdminUser } from '../../types/adminUsers'
import { MAX_DISPLAY_NAME_CHARS, readDisplayName, readJobLimit, readPaidLimit, type Parsed } from './adminUsers'

type Status = { kind: 'idle' } | { kind: 'saving' } | { kind: 'saved' } | { kind: 'error' | 'hint'; text: string }

/**
 * Enter or leaving the field sends it. "✓ Saved" shows only once the server has answered, and a refusal keeps
 * what was typed beside the reason, so nothing is lost and nothing looks saved that wasn't. Esc puts the saved
 * value back.
 */
export function AutoSaveField<T extends string | number | null>({ id, label, help, saved, toText, parse, onSave, placeholder, inputMode, maxLength, size, clearOnEnterOnly }: {
  id: string
  label: string
  help?: string
  saved: T
  toText: (v: T) => string
  parse: (text: string) => Parsed<T>
  onSave: (value: T) => Promise<unknown>
  placeholder?: string
  inputMode?: 'text' | 'decimal' | 'numeric'
  maxLength?: number
  size?: number
  /** Emptying the field removes a limit: only Enter does that, so a field emptied to retype and then left keeps it. */
  clearOnEnterOnly?: boolean
}) {
  const [text, setText] = useState(() => toText(saved))
  const [status, setStatus] = useState<Status>({ kind: 'idle' })
  // A new saved value (this field's save, or a reload) replaces the text only if the person hasn't typed over it.
  const [seen, setSeen] = useState(saved)
  if (seen !== saved) {
    setSeen(saved)
    if (text === toText(seen)) setText(toText(saved))
  }
  // One save at a time: Enter then leaving the field would otherwise send it twice.
  const sending = useRef(false)
  // The text the server last refused, so leaving the field doesn't send it again; typing or Enter tries again.
  const refused = useRef<string | null>(null)

  const commit = (byEnter: boolean) => {
    // Enter still retries a refused value: the refusal may have been passing (a restart, a dropped connection).
    if (sending.current || (!byEnter && text === refused.current)) return
    const parsed = parse(text)
    if ('problem' in parsed) {
      setStatus({ kind: 'error', text: parsed.problem })
      return
    }
    if (clearOnEnterOnly && !byEnter && parsed.value === null && saved !== null) {
      setStatus({ kind: 'hint', text: 'Press Enter to remove the limit, or Esc to keep it.' })
      return
    }
    if (parsed.value === saved) {
      if (status.kind === 'error' || status.kind === 'hint') setStatus({ kind: 'idle' })
      return
    }
    const sent = text
    sending.current = true
    setStatus({ kind: 'saving' })
    onSave(parsed.value).then(
      () => {
        sending.current = false
        setStatus({ kind: 'saved' })
        setText(toText(parsed.value))
      },
      (e: unknown) => {
        sending.current = false
        refused.current = sent
        const { title, detail } = describeError(e)
        setStatus({ kind: 'error', text: detail ? `${title} ${detail}` : title })
      },
    )
  }

  const bad = status.kind === 'error'
  return (
    <div className="admin-person-field">
      <label htmlFor={id}>{label}</label>
      {help && <HelpTip label={label} id={`${id}-tip`}>{help}</HelpTip>}
      <input id={id} type="text" autoComplete="off" inputMode={inputMode} maxLength={maxLength} size={size}
        value={text} placeholder={placeholder}
        // Read-only while its save is on the way, so the answer always describes the text on screen; focus stays.
        readOnly={status.kind === 'saving'} aria-busy={status.kind === 'saving' || undefined}
        aria-invalid={bad || undefined} aria-describedby={`${id}-state`}
        onChange={(e) => {
          setText(e.target.value)
          refused.current = null
          setStatus({ kind: 'idle' })
        }}
        onBlur={() => commit(false)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            commit(true)
          } else if (e.key === 'Escape' && status.kind !== 'saving') {
            refused.current = null
            setText(toText(saved))
            setStatus({ kind: 'idle' })
          }
        }} />
      <span id={`${id}-state`} role="status" className={bad ? 'admin-field-state error' : status.kind === 'saved' ? 'admin-field-state admin-field-saved' : 'admin-field-state muted'}>
        {status.kind === 'saving' ? 'Saving…' : status.kind === 'saved' ? '✓ Saved' : status.kind === 'error' || status.kind === 'hint' ? status.text : ''}
      </span>
    </div>
  )
}

const limitText = (v: number | null) => (v === null ? '' : String(v))

export function PersonFields({ user: u, limits, onName, onPaidLimit, onJobLimit }: {
  user: AdminUser
  /** Members only: an admin holds every right, so limits don't apply to them. */
  limits: boolean
  onName: (name: string) => Promise<unknown>
  onPaidLimit: (usd: number | null) => Promise<unknown>
  onJobLimit: (jobs: number | null) => Promise<unknown>
}) {
  return (
    <div className="admin-person-fields">
      <AutoSaveField id={`admin-user-name-${u.id}`} label="Name" saved={u.display_name ?? ''} toText={(v) => v}
        parse={readDisplayName} onSave={onName} placeholder="No name" maxLength={MAX_DISPLAY_NAME_CHARS} size={18} />
      {limits && (
        <AutoSaveField id={`admin-paid-limit-${u.id}`} label="Paid limit a month (USD)" saved={u.monthly_paid_cap_usd ?? null}
          toText={limitText} parse={readPaidLimit} onSave={onPaidLimit} placeholder="No limit" inputMode="decimal" size={8} clearOnEnterOnly
          help={'Paid engines run on this PC\'s keys. Once this person\'s spending this month reaches the limit, paid engines are off for them until next month, and a running paid job of theirs stops. The monthly cap in Settings still applies to everyone. Leave it empty for no limit.'} />
      )}
      {limits && (
        <AutoSaveField id={`admin-job-limit-${u.id}`} label="Jobs a day" saved={u.daily_job_limit ?? null}
          toText={limitText} parse={readJobLimit} onSave={onJobLimit} placeholder="No limit" inputMode="numeric" size={8} clearOnEnterOnly
          help="Every background job counts: a transcription, a translation, a download. Once they have started this many today, new ones are refused until tomorrow. Jobs already running finish. The count starts again at midnight on this PC. There is no daily limit unless you set a number. With or without one, they can't have more than 2 jobs running and 10 waiting at once." />
      )}
    </div>
  )
}
