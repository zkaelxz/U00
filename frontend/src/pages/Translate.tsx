import { useCallback, useEffect, useState } from 'react'

import { ApiError } from '../api/client'
import {
  NON_ENGLISH_LANGUAGES,
  engineShortName,
  engineSummary,
  languagePair,
  translateApi,
  usableEngines,
  validateTranslateInput,
} from '../api/translate'
import { ConfirmButton } from '../components/ConfirmButton'
import { ErrorBanner } from '../components/ErrorBanner'
import { usePcOnly } from '../hooks/usePcOnly'
import { DownloadResultButton, OpenFileField } from './TranslateFileControls'
import './translate.css'
import type { TranslateDirection, TranslateEngine, TranslateHistoryEntry } from '../types/translate'

export default function TranslatePage() {
  const [engines, setEngines] = useState<TranslateEngine[]>([])
  const [history, setHistory] = useState<TranslateHistoryEntry[]>([])
  const [engine, setEngine] = useState('')
  const [model, setModel] = useState('')
  const [direction, setDirection] = useState<TranslateDirection>('to_english')
  const [other, setOther] = useState('zh')
  const [text, setText] = useState('')
  const [result, setResult] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(false)
  const [sourceName, setSourceName] = useState<string | null>(null)
  const [fileMessage, setFileMessage] = useState<string | null>(null)
  const [resultTarget, setResultTarget] = useState('en')
  const [clearing, setClearing] = useState(false)
  const [clearError, setClearError] = useState<unknown>(null)
  const pc = usePcOnly()

  const refreshHistory = useCallback(() => translateApi.history().then(setHistory, setError), [])

  useEffect(() => {
    translateApi.engines().then((all) => {
      setEngines(all)
      setEngine((cur) => cur || usableEngines(all)[0]?.name || '')
    }, setError)
    refreshHistory()
  }, [refreshHistory])

  const clearHistory = () => {
    setClearing(true)
    setClearError(null)
    translateApi
      .clearHistory()
      .then(() => refreshHistory(), setClearError)
      .finally(() => setClearing(false))
  }

  const usable = usableEngines(engines)
  const selected = usable.find((e) => e.name === engine)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    const invalid = validateTranslateInput(text, engine)
    if (invalid) {
      setError(new ApiError(422, { code: 'validation_error', message: invalid }))
      return
    }
    setError(null)
    setResult(null)
    setLoading(true)
    const pair = languagePair(direction, other)
    try {
      setResult(
        await translateApi.translate({
          text,
          engine,
          ...pair,
          model: model || null,
        }),
      )
      setResultTarget(pair.target_language)
      refreshHistory()
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="panel page-narrow" aria-label="Translate">
      <h2>Translate</h2>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <form onSubmit={submit}>
        <div className="field-row">
          <label className="field">
            <span>Engine</span>
            <select
              aria-label="Engine"
              value={engine}
              onChange={(e) => {
                setEngine(e.target.value)
                setModel('')
              }}
            >
              {usable.map((en) => (
                <option key={en.name} value={en.name}>
                  {engineShortName(en)}
                </option>
              ))}
            </select>
          </label>
          {selected?.models && (
            <label className="field">
              <span>Model</span>
              <select value={model} onChange={(e) => setModel(e.target.value)}>
                <option value="">Default</option>
                {selected.models.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="field">
            <span>Direction</span>
            <select
              value={direction}
              onChange={(e) => setDirection(e.target.value as TranslateDirection)}
            >
              <option value="to_english">Into English</option>
              <option value="from_english">From English</option>
            </select>
          </label>
          <label className="field">
            <span>{direction === 'to_english' ? 'Source language' : 'Target language'}</span>
            <select value={other} onChange={(e) => setOther(e.target.value)}>
              {NON_ENGLISH_LANGUAGES.map((l) => (
                <option key={l.code} value={l.code}>
                  {l.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <details className="engine-details">
          <summary>Engine details</summary>
          <ul aria-label="Engine keys">
            {engines.map((en) => (
              <li key={en.name}>
                <div className="engine-head">
                  <strong>{engineShortName(en)}</strong>
                  <span className={en.key_configured ? 'badge ok' : 'badge bad'}>
                    key configured: {en.key_configured ? 'yes' : 'no'}
                  </span>
                </div>
                <div className="muted">{engineSummary(en.label)}</div>
              </li>
            ))}
          </ul>
        </details>
        <OpenFileField
          sourceName={sourceName}
          message={fileMessage}
          target={{ setText, setSourceName, setFileMessage }}
        />
        <label className="field">
          <span>Text to translate</span>
        <textarea
          aria-label="Text to translate"
          rows={6}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        </label>
        <div className="actions">
          <button type="submit" className="primary" disabled={loading}>
            {loading ? 'Translating…' : 'Translate'}
          </button>
        </div>
      </form>
      {result !== null && (
        <div>
          <h3>Result</h3>
          <pre data-testid="translate-result" className="result">
            {result}
          </pre>
          <div className="actions">
            <DownloadResultButton
              result={result}
              sourceName={sourceName}
              targetLanguage={resultTarget}
            />
          </div>
        </div>
      )}
      <h3>History</h3>
      {history.length > 0 &&
        (pc === 'remote' ? (
          <p className="muted">Clearing history is PC only.</p>
        ) : (
          <div className="actions">
            <ConfirmButton
              name="translation history"
              label="Clear history…"
              ariaLabel="Clear history"
              verb="clear"
              busy={clearing}
              onConfirm={clearHistory}
            />
          </div>
        ))}
      <ErrorBanner error={clearError} describe={{ pcOnly: true }} onDismiss={() => setClearError(null)} />
      {history.length === 0 ? (
        <p className="muted">No translations yet.</p>
      ) : (
        <ul data-testid="translate-history" className="history-list">
          {history.map((h, i) => (
            <li key={`${h.created_at}-${i}`}>
              <span className="muted">
                {h.source_language} → {h.target_language} · {h.engine}
              </span>
              <div>{h.source_text}</div>
              <div>{h.translated_text}</div>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
