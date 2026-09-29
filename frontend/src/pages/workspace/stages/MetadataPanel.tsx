import { useState } from 'react'

import { analyzeMedia, applyMetadata, suggestMetadata } from '../../../api/metadata'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { Section } from '../../../components/Section'
import type { MediaAnalysis } from '../../../types/workspace'
import {
  acceptedFields,
  analysisDetails,
  analysisSummary,
  autofillRequest,
  defaultSelection,
  suggestionRows,
  type SuggestionRow,
} from '../metadataForm'
import { useStage } from '../StageContext'

export function AutofillPanel() {
  const { dramaId, drama, refetchDrama } = useStage()
  const [url, setUrl] = useState('')
  const [text, setText] = useState('')
  const [rows, setRows] = useState<SuggestionRow[] | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const req = autofillRequest(url, text)
  const problem = url.trim() && text.trim() ? 'Use either a URL or pasted text, not both.' : null

  const suggest = () => {
    if (!req) return
    setBusy(true)
    setNotice(null)
    suggestMetadata(dramaId, req).then(
      (r) => {
        const next = suggestionRows(r.suggestion, drama)
        setError(null)
        setRows(next)
        setSelected(defaultSelection(next))
        if (!next.length) setNotice('No metadata found.')
        setBusy(false)
      },
      (e: unknown) => {
        setError(e)
        setBusy(false)
      },
    )
  }

  const apply = () => {
    if (!rows) return
    applyMetadata(dramaId, acceptedFields(rows, selected)).then(
      () => {
        setError(null)
        setRows(null)
        setNotice('Metadata updated.')
        refetchDrama()
      },
      setError,
    )
  }

  const toggle = (key: string) =>
    setSelected((s) => {
      const n = new Set(s)
      if (!n.delete(key)) n.add(key)
      return n
    })

  return (
    <section className="panel" aria-label="Auto-fill metadata">
      <Section storageKey="source.autofill" title="Auto-fill metadata" summary="from a listing page or pasted text">
        <div className="source-panel">
          <Field label="Listing URL" help="A public http(s) page. Nothing is saved until you accept the suggestions.">
            <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} />
          </Field>
          <Field label="Or paste page text" help="Use this when the page needs a login or JavaScript.">
            <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} />
          </Field>
          {problem && <p className="error" role="alert">{problem}</p>}
          <div>
            <button type="button" disabled={!req || busy} onClick={suggest}>
              Auto-fill
            </button>
          </div>
          {rows && rows.length > 0 && (
            <>
              <ul className="source-suggestions" aria-label="Suggested metadata">
                {rows.map((r) => (
                  <li key={r.key}>
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={selected.has(r.key)}
                        disabled={r.same}
                        onChange={() => toggle(r.key)}
                      />
                      <span>
                        <strong>{r.label}:</strong> {r.suggested}
                        {r.same && <span className="muted"> (already set)</span>}
                        {r.conflict && <span className="muted"> (replaces: {r.current})</span>}
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
              <div className="source-file">
                <button type="button" disabled={!Object.keys(acceptedFields(rows, selected)).length} onClick={apply}>
                  Apply selected
                </button>
                <button type="button" onClick={() => setRows(null)}>
                  Ignore
                </button>
              </div>
            </>
          )}
          {notice && <p role="status">{notice}</p>}
          <ErrorBanner error={error} onDismiss={() => setError(null)} />
        </div>
      </Section>
    </section>
  )
}

export function AnalyzePanel({ hasMedia }: { hasMedia: boolean }) {
  const { dramaId } = useStage()
  const [result, setResult] = useState<MediaAnalysis | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  const run = () => {
    setBusy(true)
    analyzeMedia(dramaId).then(
      (r) => {
        setError(null)
        setResult(r)
        setBusy(false)
      },
      (e: unknown) => {
        setError(e)
        setBusy(false)
      },
    )
  }

  return (
    <section className="panel" aria-label="Analyze media">
      <Section
        storageKey="source.analyze"
        title="Analyze media"
        summary={result ? analysisSummary(result) : hasMedia ? 'not analyzed' : 'upload a file first'}
      >
        <div className="source-panel">
          <div>
            <button type="button" disabled={!hasMedia || busy} onClick={run}>
              Analyze media
            </button>
          </div>
          {result && (
            <dl className="source-analysis" data-testid="analysis">
              {analysisDetails(result).map(([k, v]) => (
                <div key={k}>
                  <dt>{k}</dt>
                  <dd>{v}</dd>
                </div>
              ))}
            </dl>
          )}
          <ErrorBanner error={error} onDismiss={() => setError(null)} />
        </div>
      </Section>
    </section>
  )
}
