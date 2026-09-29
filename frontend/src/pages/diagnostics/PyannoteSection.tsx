import { useEffect, useState } from 'react'

import { getPyannote } from '../../api/diagnostics'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Section } from '../../components/Section'
import type { DiagnosticsPyannoteReadiness } from '../../types/diagnostics'
import { hfModelUrl, pyannoteSummary } from './diagnosticsAdmin'

/** "Speaker detection": pyannote installed, token set, and (on request) gated models open. */
export function PyannoteSection() {
  const [data, setData] = useState<DiagnosticsPyannoteReadiness | null>(null)
  const [checked, setChecked] = useState(false)
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    getPyannote().then(setData, setError)
  }, [])

  const checkAccess = () => {
    setChecking(true)
    setError(null)
    getPyannote(true).then(
      (r) => {
        setData(r)
        setChecked(true)
        setChecking(false)
      },
      (e: unknown) => {
        setError(e)
        setChecking(false)
      },
    )
  }

  return (
    <Section title="Speaker detection" storageKey="diagnostics.pyannote" summary={data ? pyannoteSummary(data) : undefined}>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {!data ? (
        !error && <p className="muted">Loading…</p>
      ) : (
        <div className="diag-stack">
          <ul className="diag-rows">
            <li>pyannote: {data.pyannote_installed ? 'installed' : 'not installed'}</li>
            <li>Hugging Face token: {data.hf_token_configured ? 'set' : 'not set'}</li>
          </ul>
          <Field label="Gated models" help="Asks Hugging Face using the saved token. Nothing else on this page goes online.">
            <button type="button" aria-label="Check access online" disabled={checking} onClick={checkAccess}>
              {checking ? 'Checking…' : 'Check access online'}
            </button>
          </Field>
          {checked && data.models === null && (
            <p className="muted">Can't check: huggingface_hub isn't installed.</p>
          )}
          {data.models && (
            <ul className="diag-rows" aria-label="Gated models">
              {data.models.map((m) => (
                <li key={m.model}>
                  {m.accessible ? (
                    `${m.model}: open`
                  ) : (
                    <>
                      <span className="warn">{m.model}: terms not accepted</span>{' '}
                      <a className="hf-terms" href={hfModelUrl(m.model)} target="_blank" rel="noopener noreferrer">
                        Accept terms ↗
                      </a>
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Section>
  )
}
