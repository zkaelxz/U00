import { useId, useState } from 'react'

import {
  cancelSpellingSuggestions,
  getNameConsistency,
  getSpellingSuggestions,
  saveGlossaryTerm,
  startSpellingSuggestions,
} from '../../../api/translateStage'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { buttonClass } from '../../../components/uiClasses'
import { lineNumber } from '../../../lineNumber'
import type { GlossaryTerm, NameConsistency } from '../../../types/translateStage'
import { useStage } from '../StageContext'
import { isActiveStatus } from './autotuneGlossary'
import { aliasBlocker, aliasesWith } from './glossaryVariantsModel'
import { consistencySummary, spellingsEmptyText, statusForTerm } from './glossarySpellingsModel'
import { useRunStatus } from './useRunStatus'
import './glossaryVariants.css'

// The owner types the name in English and asks for guesses. The model's guesses are kept
// only when they occur in this title's lines; each one is added as an alias after a
// confirm, through the normal term update. The consistency check is read-only.
export function SuggestSpellings({ term, initialEnglish, onEnglish, onAdded, onClose }: {
  term: GlossaryTerm
  // The English name typed earlier, so reopening the panel doesn't lose it.
  initialEnglish?: string
  onEnglish?: (english: string) => void
  onAdded: () => void
  onClose: () => void
}) {
  const { dramaId } = useStage()
  const inputId = useId()
  const [english, setEnglish] = useState(initialEnglish ?? term.term_translation)
  const run = useRunStatus(dramaId, getSpellingSuggestions)
  const status = statusForTerm(run.status, term.term_original)
  const active = run.status != null && isActiveStatus(run.status.status)
  const [error, setError] = useState<unknown>(null)
  const [starting, setStarting] = useState(false)
  const [aliases, setAliases] = useState(term.aliases)
  const [added, setAdded] = useState<string[]>([])
  const [confirming, setConfirming] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [check, setCheck] = useState<NameConsistency | null>(null)
  const [checking, setChecking] = useState(false)

  const start = () => {
    setStarting(true)
    setError(null)
    startSpellingSuggestions(dramaId, term.id, english.trim())
      .then(() => run.refresh(), setError)
      .finally(() => setStarting(false))
  }
  const cancel = () => {
    const runId = run.status?.run_id
    if (runId) cancelSpellingSuggestions(dramaId, runId).then(() => run.refresh(), setError)
  }
  const add = (text: string) => {
    setSaving(true)
    saveGlossaryTerm(dramaId, { id: term.id, aliases: aliasesWith(aliases, text) })
      .then(
        (saved) => {
          setError(null)
          setAliases(saved.aliases)
          setAdded((cur) => [...cur, text])
          setConfirming(null)
          onAdded()
        },
        setError,
      )
      .finally(() => setSaving(false))
  }
  const runCheck = () => {
    setChecking(true)
    getNameConsistency(dramaId, term.id, english.trim())
      .then((r) => { setError(null); setCheck(r) }, setError)
      .finally(() => setChecking(false))
  }

  const emptyText = spellingsEmptyText(status)
  const proposals = status?.status === 'done' ? status.result?.proposals ?? [] : []
  const failed = status?.status === 'error' || status?.status === 'cancelled'

  return (
    <div className="glossary-variants" role="region" aria-label={`Suggest spellings of ${term.term_original}`}>
      <h2 className="subhead">Spellings of “{term.term_original}”</h2>
      <p className="muted">
        Type the name in English. One request to this title’s engine guesses how it may have been written in the
        transcript (this can cost money on a paid engine). Only guesses that really appear in the lines are listed,
        and nothing is added until you confirm it.
      </p>
      <Field label="Name in English" help="Used to ask for likely spellings and to check the English lines.">
        <input id={inputId} value={english} maxLength={80} onChange={(e) => {
          setEnglish(e.target.value)
          onEnglish?.(e.target.value)
        }} />
      </Field>
      <div className="actions">
        <button type="button" className={buttonClass('primary', 'sm')} disabled={!english.trim() || active || starting} onClick={start}>
          {starting ? 'Starting…' : 'Suggest spellings'}
        </button>
        {active && (
          <button type="button" className={buttonClass('secondary', 'sm')} onClick={cancel}>Cancel</button>
        )}
        <button
          type="button"
          className={buttonClass('secondary', 'sm')}
          disabled={!english.trim() || checking}
          title="Lists lines where this name is not written as above in the English. Free; nothing is changed."
          onClick={runCheck}
        >
          {checking ? 'Checking…' : 'Check English names'}
        </button>
      </div>
      <ErrorBanner error={error ?? run.error} onDismiss={() => { setError(null); run.clearError() }} />
      {active && <p role="status">{run.status?.message || 'Working…'}</p>}
      {failed && <p role="status" className="muted">{status?.status === 'cancelled' ? 'Cancelled. Nothing was changed.' : status?.message}</p>}
      {emptyText && <p role="status">{emptyText}</p>}
      {status?.result?.truncated && <p className="muted">Only the first {status.result.lines_scanned} lines were checked.</p>}
      {proposals.length > 0 && (
        <ul className="glossary-variants-list" aria-label="Verified spellings">
          {proposals.map((c) => {
            const blocker = aliasBlocker({ is_alias: c.is_alias || added.includes(c.text), is_term: c.is_term })
            return (
              <li key={c.text}>
                <div>
                  <strong>{c.text}</strong>{' '}
                  <span className="muted">{c.count} mention{c.count === 1 ? '' : 's'} in the lines</span>
                </div>
                <ul className="glossary-variants-examples muted" aria-label="Example lines">
                  {c.examples.map((e, i) => <li key={`${e.line_idx}-${i}`}>#{lineNumber(e.line_idx)} {e.snippet}</li>)}
                </ul>
                {confirming === c.text ? (
                  <div className="actions" role="group" aria-label={`Confirm alias ${c.text}`}>
                    <span role="alert">Add “{c.text}” as an alias of “{term.term_original}”?</span>
                    <button type="button" className={buttonClass('primary', 'sm')} disabled={saving} onClick={() => add(c.text)}>
                      {saving ? 'Adding…' : 'Yes, add alias'}
                    </button>
                    <button type="button" className={buttonClass('secondary', 'sm')} disabled={saving} onClick={() => setConfirming(null)}>Cancel</button>
                  </div>
                ) : blocker ? (
                  <span className="muted">{added.includes(c.text) ? 'Added as an alias.' : blocker}</span>
                ) : (
                  <div className="actions">
                    <button
                      type="button"
                      className={buttonClass('secondary', 'sm')}
                      title="Future glossary matches will count this spelling as the same term"
                      aria-label={`Add ${c.text} as alias`}
                      onClick={() => setConfirming(c.text)}
                    >
                      Add as alias
                    </button>
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
      {check && (
        <div role="region" aria-label="English name check" className="glossary-variants">
          <p role="status">{consistencySummary(check)}</p>
          {check.available && check.other_words.length > 0 && (
            <p className="muted">Other capitalised words in those lines: {check.other_words.join(', ')}.</p>
          )}
          {check.truncated && <p className="muted">Only the first {check.lines_scanned} lines were checked.</p>}
          {check.more_rows && <p className="muted">Only the first {check.differing.length} differing lines are shown.</p>}
          {check.differing.length > 0 && (
            <ul className="glossary-variants-list" aria-label="Lines that differ">
              {check.differing.map((r) => (
                <li key={r.line_idx}>
                  <span className="muted">#{lineNumber(r.line_idx)} {r.source}</span>
                  <span>{r.english}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      <div className="actions">
        <button type="button" className={buttonClass('ghost', 'sm')} onClick={onClose}>Close</button>
      </div>
    </div>
  )
}
