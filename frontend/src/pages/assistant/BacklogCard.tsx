// The maintenance backlog: notes, bugs and feature ideas kept on the PC.
// Items come from the viewer (or an assistant suggestion they chose to add);
// deleting is two-step (ConfirmButton).
import { useState } from 'react'

import { MAX_BACKLOG_TEXT, clearBacklog, deleteBacklogItem } from '../../api/assistant'
import { Badge } from '../../components/Badge'
import { Section } from '../../components/Section'
import { ConfirmButton } from '../../components/ConfirmButton'
import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'
import type { BacklogItem, BacklogKind } from '../../types/assistant'
import { BACKLOG_KINDS, assistantErrorText, kindLabel, plural, shortDate } from './assistantFormat'

type Props = {
  items: BacklogItem[] | null
  loadError: string | null
  onItems: (update: (cur: BacklogItem[] | null) => BacklogItem[] | null) => void
  onAdd: (kind: BacklogKind, text: string) => Promise<BacklogItem>
}

const excerpt = (text: string, n = 40) => (text.length > n ? `${text.slice(0, n - 1)}…` : text)

export function BacklogCard({ items, loadError, onItems, onAdd }: Props) {
  const [kind, setKind] = useState<BacklogKind>('bug')
  const [text, setText] = useState('')
  const [adding, setAdding] = useState(false)
  const [busyId, setBusyId] = useState<number | 'all' | null>(null)
  const [error, setError] = useState<string | null>(null)

  const add = () => {
    const t = text.trim()
    if (!t) return
    setAdding(true)
    setError(null)
    onAdd(kind, t).then(
      () => {
        setText('')
        setAdding(false)
      },
      (e: unknown) => {
        setError(assistantErrorText(e))
        setAdding(false)
      },
    )
  }

  const remove = (id: number) => {
    setBusyId(id)
    setError(null)
    deleteBacklogItem(id).then(
      () => {
        onItems((cur) => (cur ?? []).filter((x) => x.id !== id))
        setBusyId(null)
      },
      (e: unknown) => {
        setError(assistantErrorText(e))
        setBusyId(null)
      },
    )
  }

  const clearAll = () => {
    setBusyId('all')
    setError(null)
    clearBacklog().then(
      () => {
        onItems(() => [])
        setBusyId(null)
      },
      (e: unknown) => {
        setError(assistantErrorText(e))
        setBusyId(null)
      },
    )
  }

  const count = items?.length ?? 0

  return (
    <section aria-label="Backlog">
      {/* Outside the fold so a load failure is seen without opening the section. */}
      {loadError && (
        <p className="error" role="alert">
          {loadError}
        </p>
      )}
      <Section title="Backlog" summary={items ? plural(count, 'item') : 'Notes for later'} storageKey="assistant.backlog">
      {count > 0 && (
        <div className="assistant-actions">
          <ConfirmButton label="Clear all…" ariaLabel="Clear all backlog items" name="all backlog items" verb="clear" busy={busyId === 'all'} disabled={busyId !== null && busyId !== 'all'} onConfirm={clearAll} />
        </div>
      )}
      {!items ? (
        !loadError && <p className="muted">Loading…</p>
      ) : count === 0 ? (
        <p className="muted">Nothing in the backlog yet.</p>
      ) : (
        <ul className="assistant-backlog" aria-label="Backlog items">
          {items.map((it) => {
            const { label, tone } = kindLabel(it.kind)
            return (
              <li key={it.id}>
                <div className="assistant-backlog-head">
                  <Badge tone={tone}>{label}</Badge>
                  <span className="muted assistant-date">{shortDate(it.created_at)}</span>
                </div>
                <p className="assistant-backlog-text">{it.text}</p>
                <ConfirmButton
                  name={excerpt(it.text)}
                  ariaLabel={`Delete backlog item: ${excerpt(it.text)}`}
                  confirmLabel="Confirm delete"
                  busy={busyId === it.id}
                  disabled={busyId !== null && busyId !== it.id}
                  onConfirm={() => remove(it.id)}
                />
              </li>
            )
          })}
        </ul>
      )}
      <form
        className="assistant-backlog-add"
        onSubmit={(e) => {
          e.preventDefault()
          add()
        }}
      >
        <Field label="Kind">
          <select value={kind} onChange={(e) => setKind(e.target.value as BacklogKind)}>
            {BACKLOG_KINDS.map((k) => (
              <option key={k.kind} value={k.kind}>
                {k.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="New item">
          <input
            type="text"
            maxLength={MAX_BACKLOG_TEXT}
            value={text}
            placeholder="What to fix or add"
            onChange={(e) => setText(e.target.value)}
          />
        </Field>
        <button type="submit" className={buttonClass('primary')} disabled={adding || !text.trim()}>
          {adding ? 'Adding…' : 'Add'}
        </button>
      </form>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      </Section>
    </section>
  )
}
