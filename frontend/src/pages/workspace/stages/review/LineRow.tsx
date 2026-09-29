import { useState } from 'react'

import { ApiError } from '../../../../api/client'
import { addNote, dismissFlag, patchLine } from '../../../../api/review'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import type { ReviewLine } from '../../../../types/review'
import { buildPatch, CONFLICT_MESSAGE, draftFromLine, formatTime, type LineDraft } from './reviewLogic'

interface Props {
  dramaId: number
  line: ReviewLine
  onChanged: () => void
}

export function LineRow({ dramaId, line, onChanged }: Props) {
  const [draft, setDraft] = useState<LineDraft | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [conflict, setConflict] = useState(false)
  const [note, setNote] = useState<{ term: string; type: string; text: string } | null>(null)

  const fail = (e: unknown) => {
    if (e instanceof ApiError && e.status === 409) {
      setConflict(true)
      setError(null)
    } else setError(e)
  }
  const set = (k: keyof LineDraft, v: string) => draft && setDraft({ ...draft, [k]: v })

  const save = () => {
    if (!draft) return
    const patch = buildPatch(line, draft)
    if (typeof patch === 'string') return setProblem(patch)
    setProblem(null)
    if (patch === null) return setDraft(null)
    patchLine(dramaId, line.id, patch).then(() => {
      setDraft(null)
      setError(null)
      setConflict(false)
      onChanged()
    }, fail)
  }

  const dismiss = () =>
    dismissFlag(dramaId, line.id).then(() => {
      setError(null)
      onChanged()
    }, fail)

  const saveNote = () => {
    if (!note) return
    addNote(dramaId, { line_id: line.id, term: note.term, note_type: note.type, note: note.text }).then(
      () => {
        setNote(null)
        setError(null)
        onChanged()
      },
      fail,
    )
  }

  return (
    <li className="review-line" data-testid={`line-${line.id}`}>
      <div className="review-line-meta muted">
        #{line.idx} · {formatTime(line.start)}–{formatTime(line.end)}
        {line.speaker ? ` · ${line.speaker}` : ''}
        {line.sfx ? ' · sound cue' : ''}
        {line.dub_filename ? ` · dub: ${line.dub_filename}` : ''}
      </div>
      {line.flag && (
        <div className="review-flag" data-testid="line-flag">
          Flagged: {line.flag}
          {line.flag_note ? ` (${line.flag_note})` : ''}{' '}
          <button type="button" className="link" onClick={dismiss}>
            Dismiss flag
          </button>
        </div>
      )}
      {draft ? (
        <div className="review-edit">
          <label>
            Source
            <textarea value={draft.zh} onChange={(e) => set('zh', e.target.value)} rows={2} />
          </label>
          <label>
            Translation
            <textarea value={draft.en} onChange={(e) => set('en', e.target.value)} rows={2} />
          </label>
          <label>
            Speaker
            <input value={draft.speaker} onChange={(e) => set('speaker', e.target.value)} />
          </label>
          <label>
            Start (s)
            <input value={draft.start} onChange={(e) => set('start', e.target.value)} />
          </label>
          <label>
            End (s)
            <input value={draft.end} onChange={(e) => set('end', e.target.value)} />
          </label>
          {problem && <p className="error" role="alert">{problem}</p>}
          <div className="review-actions">
            <button type="button" onClick={save}>Save line</button>
            <button type="button" onClick={() => { setDraft(null); setProblem(null) }}>Cancel</button>
          </div>
        </div>
      ) : (
        <div className="review-text">
          <div lang="zh">{line.zh}</div>
          <div data-testid="line-en">{line.en || <span className="muted">(not translated)</span>}</div>
          <div className="review-actions">
            <button type="button" onClick={() => { setDraft(draftFromLine(line)); setConflict(false) }}>
              Edit
            </button>
            <button type="button" onClick={() => setNote(note ? null : { term: '', type: 'translation', text: '' })}>
              Add note
            </button>
          </div>
        </div>
      )}
      {note && (
        <div className="review-edit">
          <label>
            Term
            <input value={note.term} onChange={(e) => setNote({ ...note, term: e.target.value })} />
          </label>
          <label>
            Type
            <input value={note.type} onChange={(e) => setNote({ ...note, type: e.target.value })} />
          </label>
          <label>
            Note
            <textarea value={note.text} onChange={(e) => setNote({ ...note, text: e.target.value })} rows={2} />
          </label>
          <button type="button" disabled={!note.term.trim() || !note.text.trim()} onClick={saveNote}>
            Save note
          </button>
        </div>
      )}
      {conflict && (
        <div className="banner error-banner" role="alert" data-testid="line-conflict">
          <span>{CONFLICT_MESSAGE}</span>
          <button type="button" className="link" onClick={() => { setDraft(null); setConflict(false); onChanged() }}>
            Reload
          </button>
        </div>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </li>
  )
}
