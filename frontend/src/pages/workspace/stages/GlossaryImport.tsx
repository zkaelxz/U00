import { useState } from 'react'

import { glossaryCsvUrl, importGlossary } from '../../../api/translateStage'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { Toggle } from '../../../components/Toggle'
import { buttonClass } from '../../../components/uiClasses'
import { usePcOnly } from '../../../hooks/usePcOnly'
import type { GlossaryImportResult } from '../../../types/translateStage'
import { useStage } from '../StageContext'
import { GLOSSARY_FILE_ACCEPT, importSummary, validateImportText } from './glossaryImportForm'

// Parity T03/T04: Streamlit's "Import glossary file" and "Export glossary as CSV".
// A file is read in the browser and its text is sent like a paste; nothing is uploaded as a file.
// Away from the PC only pasting new terms is offered: choosing a file and replacing existing
// terms are PC-only until network zones exist (the server refuses a remote overwrite with 403).
export function GlossaryImport({ hasTerms, onImported }: { hasTerms: boolean; onImported: () => void }) {
  const { dramaId } = useStage()
  const remote = usePcOnly() === 'remote'
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
    const replace = overwrite && !remote
    importGlossary(dramaId, {
      text,
      ...(filename ? { filename } : {}),
      ...(replace ? { overwrite_existing: true, confirm: true } : {}),
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
      {!remote && (
        <Field label="Glossary file">
          <input type="file" accept={GLOSSARY_FILE_ACCEPT} onChange={(e) => load(e.target.files?.[0])} />
        </Field>
      )}
      <Field label={remote ? 'Paste the glossary' : 'Or paste the glossary'}>
        <textarea
          rows={4}
          value={text}
          onChange={(e) => {
            setText(e.target.value)
            setFilename('')
          }}
        />
      </Field>
      {remote ? (
        <p className="muted">Choosing a file and replacing existing terms are PC only; existing terms are skipped.</p>
      ) : (
        <div className="setting-list">
          <Field label="Replace terms that are already in the glossary">
            <Toggle
              checked={overwrite}
              onChange={(v) => {
                setOverwrite(v)
                setConfirming(false)
              }}
            />
          </Field>
        </div>
      )}
      <div className="glossary-bulk">
        {overwrite && !remote && confirming ? (
          <>
            <span role="alert">Existing terms with the same original text will be replaced.</span>
            <button type="button" className={buttonClass('danger')} disabled={pending} onClick={run}>
              {pending ? 'Importing…' : 'Yes, import and replace'}
            </button>
            <button type="button" className={buttonClass('secondary')} disabled={pending} onClick={() => setConfirming(false)}>Cancel</button>
          </>
        ) : (
          <button type="button" className={buttonClass('secondary')} disabled={pending} onClick={() => (overwrite && !remote ? setConfirming(true) : run())}>
            {pending ? 'Importing…' : 'Import'}
          </button>
        )}
        {hasTerms && (
          <a className={buttonClass('ghost')} href={glossaryCsvUrl(dramaId)} download>Download glossary as CSV</a>
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
