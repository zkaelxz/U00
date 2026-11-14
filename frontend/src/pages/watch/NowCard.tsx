// The Watch page's "now" card: the line being spoken, large, and the in-place fix of its English.
import type { Ref } from 'react'

import { ErrorBanner } from '../../components/ErrorBanner'
import { buttonClass } from '../../components/uiClasses'
import type { ReviewLine } from '../../types/review'
import { CONFLICT_TEXT, fieldKey, lineHeading, type LineFix } from './watchLogic'

export const NOW_HINT = 'Press play. The line being spoken shows here.'
export const NO_ENGLISH = 'No English yet'

export interface NowCardProps {
  /** The open fix's line while one is open, else the line being spoken (null before the first). */
  line: ReviewLine | null
  /** The title's language, for a line that does not carry its own. */
  sourceLanguage: string
  canEdit: boolean
  fix: LineFix | null
  busy: boolean
  /** Read out by the status region ("Saved."). */
  status: string
  onOpen: () => void
  onDraft: (text: string) => void
  onSave: () => void
  onCancel: () => void
  onAgain: () => void
  fixButtonRef?: Ref<HTMLButtonElement>
}

// No hooks here, so a test can call it and press what it returns without a browser.
export function NowCard({ line, sourceLanguage, canEdit, fix, busy, status, onOpen, onDraft, onSave, onCancel, onAgain, fixButtonRef }: NowCardProps) {
  const again = (
    <button type="button" className={buttonClass()} onClick={onAgain} disabled={busy}>Hear it again</button>
  )
  const english = line?.en ?? ''
  return (
    <section className="card watch-now" aria-label="Line being spoken">
      {line ? (
        <>
          <p className="watch-now-meta" data-testid="watch-now-meta">{lineHeading(line)}</p>
          {line.zh && <p className="watch-now-zh" lang={line.lang || sourceLanguage}>{line.zh}</p>}
          {fix ? (
            <form
              className="watch-now-form"
              onSubmit={(e) => {
                e.preventDefault()
                if (!busy) onSave()
              }}
            >
              <label className="watch-now-label" htmlFor="watch-now-field">English</label>
              <textarea
                id="watch-now-field"
                className="watch-now-field"
                data-testid="watch-now-field"
                lang="en"
                rows={3}
                autoFocus
                value={fix.draft}
                onChange={(e) => onDraft(e.target.value)}
                onKeyDown={(e) => {
                  const k = fieldKey(e)
                  if (!k || busy) return
                  e.preventDefault()
                  if (k === 'save') onSave()
                  else onCancel()
                }}
              />
              <div className="watch-now-actions">
                <button type="submit" className={buttonClass('primary')} disabled={busy} aria-keyshortcuts="Control+Enter" title="Save and play (Ctrl+Enter)">
                  {busy ? 'Saving…' : 'Save and play'}
                </button>
                <button type="button" className={buttonClass()} disabled={busy} onClick={onCancel} aria-keyshortcuts="Escape" title="Cancel (Esc)">Cancel</button>
                {again}
              </div>
            </form>
          ) : (
            <>
              <span className="watch-now-label">English</span>
              {canEdit ? (
                <button
                  type="button"
                  className={english ? 'watch-now-box' : 'watch-now-box is-empty'}
                  data-testid="watch-now-box"
                  lang={english ? 'en' : undefined}
                  title="Fix this line (E)"
                  onClick={onOpen}
                >
                  {english || NO_ENGLISH}
                </button>
              ) : (
                <p className={english ? 'watch-now-en' : 'watch-now-en is-empty'} data-testid="watch-now-en" lang={english ? 'en' : undefined}>
                  {english || NO_ENGLISH}
                </p>
              )}
              <div className="watch-now-actions">
                {canEdit && (
                  <button type="button" ref={fixButtonRef} className={buttonClass()} onClick={onOpen} aria-keyshortcuts="E" title="Fix this line (E)">
                    Fix this line
                  </button>
                )}
                {again}
              </div>
            </>
          )}
        </>
      ) : (
        <p className="muted watch-now-hint">{NOW_HINT}</p>
      )}
      {fix?.conflict && <p className="error" role="alert">{CONFLICT_TEXT}</p>}
      {fix?.error ? <ErrorBanner error={fix.error} /> : null}
      <p className="watch-now-status" role="status">{status}</p>
    </section>
  )
}
