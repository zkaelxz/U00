import { memo, useState, type KeyboardEvent } from 'react'

import { ApiError } from '../../../../api/client'
import { addNote, dismissFlag, patchLine } from '../../../../api/review'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import type { ReviewLine } from '../../../../types/review'
import { LineAi } from './LineAi'
import { buildPatch, CONFLICT_MESSAGE, draftFromLine, formatTime, suggestionPatch, type LineDraft } from './reviewLogic'

interface Props {
  dramaId: number
  line: ReviewLine
  onChanged: () => void
}

// One line as compact text. Click the English text to edit it in place
// (Enter or Ctrl+S saves, Esc cancels); "Edit details" reveals the timing,
// speaker, source, flag and note controls. The details are only rendered while
// open, so a long list stays light.
function LineRowImpl({ dramaId, line, onChanged }: Props) {
  const [draft, setDraft] = useState<LineDraft | null>(null)
  const [details, setDetails] = useState(false)
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
  const open = () => {
    setDraft((d) => d ?? draftFromLine(line))
    setConflict(false)
  }
  const close = () => {
    setDraft(null)
    setDetails(false)
    setProblem(null)
  }

  const save = () => {
    if (!draft) return
    const patch = buildPatch(line, draft)
    if (typeof patch === 'string') {
      setProblem(patch)
      setDetails(true)
      return
    }
    setProblem(null)
    if (patch === null) return close()
    patchLine(dramaId, line.id, patch).then(() => {
      close()
      setError(null)
      setConflict(false)
      onChanged()
    }, fail)
  }

  // Enter saves (Shift+Enter adds a new line), Ctrl/Cmd+S saves, Esc cancels.
  const onKey = (e: KeyboardEvent) => {
    if (e.nativeEvent.isComposing) return
    if (e.key === 'Escape') {
      e.preventDefault()
      close()
    } else if ((e.key === 's' || e.key === 'S') && (e.ctrlKey || e.metaKey)) {
      e.preventDefault()
      save()
    } else if (e.key === 'Enter' && !e.shiftKey && e.target instanceof HTMLTextAreaElement) {
      e.preventDefault()
      save()
    }
  }

  const dismiss = () =>
    dismissFlag(dramaId, line.id).then(() => {
      setError(null)
      onChanged()
    }, fail)

  // Applying an AI suggestion is the same compare-and-set patch as an edit.
  const useSuggestion = (text: string): Promise<boolean> => {
    const patch = suggestionPatch(line, text)
    if (!patch) return Promise.resolve(true)
    return patchLine(dramaId, line.id, patch).then(
      () => {
        setError(null)
        setConflict(false)
        onChanged()
        return true
      },
      (e) => {
        fail(e)
        return false
      },
    )
  }

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
    <li
      className="review-line"
      data-testid={`line-${line.id}`}
      data-flagged={line.flag ? 'true' : undefined}
      tabIndex={-1}
      onKeyDown={draft ? onKey : undefined}
    >
      <div className="review-line-meta muted">
        <span>#{line.idx}</span>
        <span>{formatTime(line.start)}–{formatTime(line.end)}</span>
        {line.speaker && <span>{line.speaker}</span>}
        {line.sfx && <span>sound cue</span>}
        {line.dub_filename && <span>dub: {line.dub_filename}</span>}
        <button
          type="button"
          className="link"
          aria-expanded={details}
          onClick={() => {
            if (details) close()
            else {
              open()
              setDetails(true)
            }
          }}
        >
          Edit details
        </button>
        {line.en && <LineAi dramaId={dramaId} line={line} onUse={useSuggestion} />}
        {line.flag && (
          <span className="review-flag" data-testid="line-flag">
            Flagged: {line.flag}
            {line.flag_note ? ` (${line.flag_note})` : ''}{' '}
            <button type="button" className="link" onClick={dismiss}>
              Dismiss flag
            </button>
          </span>
        )}
      </div>
      <div className="review-body">
        <div lang="zh" className="review-zh">{line.zh}</div>
        {draft ? (
          <div className="review-en-edit">
            <textarea
              aria-label="Translation"
              autoFocus
              value={draft.en}
              onChange={(e) => set('en', e.target.value)}
              rows={2}
            />
            <div className="review-actions">
              <button type="button" onClick={save}>Save line</button>
              <button type="button" onClick={close}>Cancel</button>
              <span className="muted review-keys">Enter save · Shift+Enter new line · Esc cancel</span>
            </div>
          </div>
        ) : (
          <button
            type="button"
            className="review-en"
            data-testid="line-en"
            title="Click to edit the translation"
            onClick={open}
          >
            {line.en || <span className="muted">(not translated)</span>}
          </button>
        )}
      </div>
      {details && draft && (
        <div className="review-edit">
          <label>
            Source
            <textarea value={draft.zh} onChange={(e) => set('zh', e.target.value)} rows={2} />
          </label>
          <div className="review-edit-row">
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
          </div>
          {problem && <p className="error" role="alert">{problem}</p>}
          <div className="review-actions">
            <button type="button" onClick={save}>Save details</button>
            <button type="button" onClick={() => setNote(note ? null : { term: '', type: 'translation', text: '' })}>
              Add note
            </button>
          </div>
        </div>
      )}
      {details && note && (
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
          <button type="button" className="link" onClick={() => { close(); setConflict(false); onChanged() }}>
            Reload
          </button>
        </div>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </li>
  )
}

export const LineRow = memo(LineRowImpl)
