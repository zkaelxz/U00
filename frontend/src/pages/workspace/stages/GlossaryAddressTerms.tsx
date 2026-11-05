import { useState } from 'react'

import { getAddressTerms, saveAddressTerms } from '../../../api/translateStage'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { buttonClass } from '../../../components/uiClasses'
import { lineNumber } from '../../../lineNumber'
import type { AddressTerms } from '../../../types/translateStage'
import { useStage } from '../StageContext'
import { pickedChoices, type Pick } from './glossaryAddressTermsModel'
import './glossaryVariants.css'

// The most frequent forms of address in the source lines that the glossary does not cover yet.
// The scan only reads; terms are added when the person presses Add, one rendering per term.
export function AddressTermsPanel({ onAdded }: { onAdded: () => void }) {
  const { dramaId } = useStage()
  const [result, setResult] = useState<AddressTerms | null>(null)
  const [picks, setPicks] = useState<Record<string, Pick>>({})
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [message, setMessage] = useState('')

  const scan = () => {
    setLoading(true)
    setMessage('')
    getAddressTerms(dramaId)
      .then((r) => { setError(null); setResult(r); setPicks({}) }, setError)
      .finally(() => setLoading(false))
  }
  const setPick = (term: string, change: Partial<Pick>) =>
    setPicks((cur) => ({ ...cur, [term]: { ...(cur[term] ?? { text: '', keep: false }), ...change } }))

  const choices = pickedChoices(picks)
  const save = () => {
    setSaving(true)
    saveAddressTerms(dramaId, choices)
      .then(
        (r) => {
          setError(null)
          setMessage(`Added ${r.saved.length} term${r.saved.length === 1 ? '' : 's'}${r.skipped_existing.length ? `, ${r.skipped_existing.length} already in the glossary` : ''}.`)
          const done = new Set([...r.saved, ...r.skipped_existing])
          setResult((cur) => cur && { ...cur, terms: cur.terms.filter((t) => !done.has(t.term)) })
          setPicks({})
          onAdded()
        },
        setError,
      )
      .finally(() => setSaving(false))
  }

  return (
    <div className="glossary-variants" role="region" aria-label="Address terms">
      <div className="actions">
        <button
          type="button"
          className={buttonClass('secondary')}
          disabled={loading}
          title="Counts forms of address such as 姐姐, さん or 오빠 in the source lines. Free; nothing is changed."
          onClick={scan}
        >
          {loading ? 'Looking…' : result ? 'Look again' : 'Find address terms'}
        </button>
        <span className="muted">Pick one English rendering per form of address so it stays the same. Nothing is added until you press Add.</span>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {message && <p role="status">{message}</p>}
      {result && !result.available && <p role="status" className="muted">No address-term list for source language “{result.language}”.</p>}
      {result?.available && result.truncated && <p className="muted">Only the first {result.lines_scanned} lines were checked.</p>}
      {result?.available && result.terms.length === 0 && <p role="status">No new address terms found.</p>}
      {result?.available && result.terms.length > 0 && (
        <>
          <ul className="glossary-variants-list">
            {result.terms.map((t) => {
              const pick = picks[t.term] ?? { text: '', keep: false }
              return (
                <li key={t.term}>
                  <div>
                    <strong>{t.term}</strong>{' '}
                    <span className="muted">{t.count} mention{t.count === 1 ? '' : 's'}</span>
                  </div>
                  <ul className="glossary-variants-examples muted" aria-label={`Example lines for ${t.term}`}>
                    {t.examples.map((e) => <li key={e.line_idx}>#{lineNumber(e.line_idx)} {e.snippet}</li>)}
                  </ul>
                  <div className="glossary-variants-form">
                    <input
                      aria-label={`English rendering of ${t.term}`}
                      placeholder="English rendering, e.g. big sister"
                      value={pick.keep ? '' : pick.text}
                      disabled={pick.keep}
                      maxLength={200}
                      onChange={(e) => setPick(t.term, { text: e.target.value })}
                    />
                    <label>
                      <input
                        type="checkbox"
                        checked={pick.keep}
                        onChange={(e) => setPick(t.term, { keep: e.target.checked })}
                      />{' '}
                      Keep as-is (romanize)
                    </label>
                  </div>
                </li>
              )
            })}
          </ul>
          <div className="actions">
            <button type="button" className={buttonClass('primary')} disabled={!choices.length || saving} onClick={save}>
              {saving ? 'Adding…' : `Add ${choices.length} term${choices.length === 1 ? '' : 's'}`}
            </button>
            <span className="muted">Saved as glossary terms in the honorific category.</span>
          </div>
        </>
      )}
    </div>
  )
}
