import { useState } from 'react'

import { glossaryCsvUrl, importGlossary } from '../../../api/translateStage'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import type { GlossaryImportResult } from '../../../types/translateStage'
import { useStage } from '../StageContext'
import { GLOSSARY_FILE_ACCEPT, importSummary, validateImportText } from './glossaryImport'

// Parity T03/T04: Streamlit's "Import glossary file" and "Export glossary as CSV".
// A file is read in the browser and its text is sent like a paste; nothing is uploaded as a file.
export function GlossaryImport({ hasTerms, onImported }: { hasTerms: boolean; onImported: () => void }) {
  const { dramaId } = useStage()
  const [text, setText] = useState('')
  const [filename, setFilename] = useState('')
  const [overwrite, setOverwrite] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [pending, setPending] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [result, setResult] = useState<GlossaryImportResult | null>(null)

  const load = (file: File | undefined) => {
    if (!file) return
    file.text().then(
      (t) => {
        setText(t)
        setFilename(file.name)
        setProblem(null)
      },
      () => setProblem('That file could not be read.'),
    )
  }

  const run = () => {
    const bad = validateImportText(text)
    if (bad) {
      setProblem(bad)
      return
    }
    setProblem(null)
    setPending(true)
    importGlossary(dramaId, {
      text,
      ...(filename ? { filename } : {}),
      ...(overwrite ? { overwrite_existing: true, confirm: true } : {}),
    })
      .then(
        (r) => {
          setError(null)
          setResult(r)
          setConfirming(false)
          onImported()
        },
        (e: unknown) => {
          setResult(null)
          setError(e)
        },
      )
      .finally(() => setPending(false))
  }

  return (
    <details className="glossary-import">
      <summary>Import or export</summary>
      <p className="muted">CSV, TSV or JSON. A two-column term, translation sheet works.</p>
      <Field label="Glossary file">
        <input type="file" accept={GLOSSARY_FILE_ACCEPT} onChange={(e) => load(e.target.files?.[0])} />
      </Field>
      <Field label="Or paste the glossary">
        <textarea
          rows={4}
          value={text}
          onChange={(e) => {
            setText(e.target.value)
            setFilename('')
          }}
        />
      </Field>
      <label className="inline">
        <input
          type="checkbox"
          checked={overwrite}
          onChange={(e) => {
            setOverwrite(e.target.checked)
            setConfirming(false)
          }}
        />{' '}
        Replace terms that are already in the glossary
      </label>
      <div className="glossary-bulk">
        {overwrite && confirming ? (
          <>
            <span role="alert">Existing terms with the same original text will be replaced.</span>
            <button type="button" className="danger" disabled={pending} onClick={run}>
              {pending ? 'Importing…' : 'Yes, import and replace'}
            </button>
            <button type="button" disabled={pending} onClick={() => setConfirming(false)}>Cancel</button>
          </>
        ) : (
          <button type="button" disabled={pending} onClick={() => (overwrite ? setConfirming(true) : run())}>
            {pending ? 'Importing…' : 'Import'}
          </button>
        )}
        {hasTerms && (
          <a href={glossaryCsvUrl(dramaId)} download>Download glossary as CSV</a>
        )}
      </div>
      {problem && <p className="error" role="alert">{problem}</p>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ serverText: true }} />
      {result && (
        <div role="status" data-testid="glossary-import-result">
          <p>{importSummary(result)}</p>
          {result.warnings.length > 0 && (
            <ul className="muted">
              {result.warnings.map((w, i) => <li key={i}>{w}</li>)}
            </ul>
          )}
        </div>
      )}
    </details>
  )
}
