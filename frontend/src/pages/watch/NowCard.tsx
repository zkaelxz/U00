// The Watch page's "now" card: the line being spoken, large, and the in-place fix of its English.
import type { MouseEvent, Ref } from 'react'

import { ErrorBanner } from '../../components/ErrorBanner'
import { buttonClass } from '../../components/uiClasses'
import type { ReviewLine } from '../../types/review'
import { LineMarkBadge } from '../workspace/stages/review/LineMarkButtons'
import { CONFLICT_TEXT, CONFLICT_UNREAD_TEXT, fieldKey, lineHeading, type LineFix, type Verdict } from './watchLogic'

const NOW_HINT = 'Press play to see lines here.'
const NO_ENGLISH = 'No English yet'

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
  /** Good, Wrong or Mixed on this line; absent for someone who may not edit lines. */
  onMark?: (verdict: Verdict) => void
  onJump?: (direction: 1 | -1) => void
  markBusy?: boolean
  fixButtonRef?: Ref<HTMLButtonElement>
  fieldRef?: Ref<HTMLTextAreaElement>
  /** The card itself takes focus after Save and play, so Space goes to the player rather than to a button. */
  cardRef?: Ref<HTMLElement>
}

// A press with the pointer leaves focus on the button, where the next Space would press it again instead of playing or
// pausing. A press from the keyboard (detail 0) keeps focus, as a button does.
const press = (e: MouseEvent<HTMLButtonElement>, act: () => void) => {
  act()
  if (e.detail !== 0) e.currentTarget.blur()
}

// No hooks here, so a test can call it and press what it returns without a browser.
export function NowCard({ line, sourceLanguage, canEdit, fix, busy, status, onOpen, onDraft, onSave, onCancel, onAgain, onMark, onJump, markBusy = false, fixButtonRef, fieldRef, cardRef }: NowCardProps) {
  const again = (
    <button type="button" className={buttonClass()} onClick={onAgain} disabled={busy} title="Hear this line again">Listen</button>
  )
  const english = line?.en ?? ''
  return (
    <section className="card watch-now" aria-label="Line being spoken" tabIndex={-1} ref={cardRef}>
      {line ? (
        <>
          <p className="watch-now-meta" data-testid="watch-now-meta">
            {lineHeading(line)}
            <LineMarkBadge line={line} />
            {line.reviewed && <span className="watch-now-checked" data-testid="watch-now-checked">✓ Looks right</span>}
            {line.flag && <span className="watch-now-flag">⚑ Flagged</span>}
          </p>
          {line.zh && <p className="watch-now-zh" lang={line.lang || sourceLanguage}>{line.zh}</p>}
          {fix ? (
            <form
              className="watch-now-form"
              onSubmit={(e) => {
                e.preventDefault()
                if (!busy) onSave()
              }}
              // On the form, so Esc cancels from Save and play, Cancel and Hear it again too, not only from the box.
              onKeyDown={(e) => {
                const k = fieldKey(e)
                if (!k || busy) return
                e.preventDefault()
                if (k === 'save') onSave()
                else onCancel()
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
                ref={fieldRef}
                // Read-only rather than disabled while saving: it keeps focus, and nothing typed then is lost when the
                // save comes back and puts back the draft it sent.
                readOnly={busy}
                value={fix.draft}
                onChange={(e) => onDraft(e.target.value)}
              />
              <div className="watch-now-actions">
                <button type="submit" className={buttonClass('primary')} disabled={busy} aria-keyshortcuts="Control+Enter" title="Ctrl+Enter">
                  {busy ? 'Saving…' : 'Save and play'}
                </button>
                <button type="button" className={buttonClass()} disabled={busy} onClick={onCancel} aria-keyshortcuts="Escape" title="Esc">Cancel</button>
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
                  title="Fix line (E)"
                  onClick={onOpen}
                >
                  {english || NO_ENGLISH}
                </button>
              ) : (
                <p className={english ? 'watch-now-en' : 'watch-now-en is-empty'} data-testid="watch-now-en" lang={english ? 'en' : undefined}>
                  {english || NO_ENGLISH}
                </p>
              )}
              {(onMark || onJump) && (
                <div className="watch-now-marks" role="group" aria-label="Your mark on this line">
                  {onMark && (
                    <>
                      <button type="button" className={buttonClass('secondary', 'md', 'watch-mark watch-mark-good')} aria-pressed={!!line.reviewed} disabled={busy || markBusy || !line.en}
                              aria-keyshortcuts="G" aria-label="Looks right" title="Looks right (G)" onClick={(e) => press(e, () => onMark('good'))}><span aria-hidden="true">✓</span></button>
                      <button type="button" className={buttonClass('secondary', 'md', 'watch-mark watch-mark-wrong')} aria-pressed={line.mark === 'wrong'} disabled={busy || markBusy || !line.en}
                              aria-keyshortcuts="X" aria-label="Wrong" title="Wrong (X)" onClick={(e) => press(e, () => onMark('wrong'))}><span aria-hidden="true">✗</span></button>
                      <button type="button" className={buttonClass('secondary', 'md', 'watch-mark watch-mark-mixed')} aria-pressed={line.mark === 'mixed'} disabled={busy || markBusy || !line.en}
                              aria-keyshortcuts="M" aria-label="Partly" title="Partly right (M)" onClick={(e) => press(e, () => onMark('mixed'))}><span aria-hidden="true">〰</span></button>
                    </>
                  )}
                  {onJump && (
                    <>
                      <button type="button" className={buttonClass()} aria-keyshortcuts="P" title="Prev. flag (P)" onClick={(e) => press(e, () => onJump(-1))}>‹ Prev. Flag</button>
                      <button type="button" className={buttonClass()} aria-keyshortcuts="N" title="Next flag (N)" onClick={(e) => press(e, () => onJump(1))}>Next Flag ›</button>
                    </>
                  )}
                </div>
              )}
              <div className="watch-now-actions">
                {canEdit && (
                  <button type="button" ref={fixButtonRef} className={buttonClass()} onClick={onOpen} aria-keyshortcuts="E">
                    Fix Line
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
      {fix?.conflict && <p className="error" role="alert">{fix.now === null ? CONFLICT_UNREAD_TEXT : CONFLICT_TEXT}</p>}
      {fix?.conflict && fix.now !== null && (
        <div className="watch-now-theirs">
          <span className="watch-now-label">It now says</span>
          <p className={fix.now ? 'watch-now-theirs-en' : 'watch-now-theirs-en is-empty'} lang={fix.now ? 'en' : undefined}>{fix.now || NO_ENGLISH}</p>
        </div>
      )}
      {fix?.error ? <ErrorBanner error={fix.error} /> : null}
      <p className="watch-now-status" role="status">{status}</p>
    </section>
  )
}
