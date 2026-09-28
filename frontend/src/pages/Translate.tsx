import { useCallback, useEffect, useState } from 'react'

import { ApiError } from '../api/client'
import {
  NON_ENGLISH_LANGUAGES,
  languagePair,
  translateApi,
  usableEngines,
  validateTranslateInput,
} from '../api/translate'
import { ErrorBanner } from '../components/ErrorBanner'
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

  const refreshHistory = useCallback(() => translateApi.history().then(setHistory, setError), [])

  useEffect(() => {
    translateApi.engines().then((all) => {
      setEngines(all)
      setEngine((cur) => cur || usableEngines(all)[0]?.name || '')
    }, setError)
    refreshHistory()
  }, [refreshHistory])

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
    try {
      setResult(
        await translateApi.translate({
          text,
          engine,
          ...languagePair(direction, other),
          model: model || null,
        }),
      )
      refreshHistory()
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="panel" aria-label="Translate">
      <h2>Translate</h2>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <form onSubmit={submit}>
        <div className="filters">
          <label>
            Engine{' '}
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
                  {en.label}
                </option>
              ))}
            </select>
          </label>
          {selected?.models && (
            <label>
              Model{' '}
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
          <label>
            Direction{' '}
            <select
              value={direction}
              onChange={(e) => setDirection(e.target.value as TranslateDirection)}
            >
              <option value="to_english">Into English</option>
              <option value="from_english">From English</option>
            </select>
          </label>
          <label>
            {direction === 'to_english' ? 'Source language' : 'Target language'}{' '}
            <select value={other} onChange={(e) => setOther(e.target.value)}>
              {NON_ENGLISH_LANGUAGES.map((l) => (
                <option key={l.code} value={l.code}>
                  {l.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <ul className="muted" aria-label="Engine keys">
          {engines.map((en) => (
            <li key={en.name}>
              {en.label}: key configured {en.key_configured ? 'yes' : 'no'}
            </li>
          ))}
        </ul>
        <textarea
          aria-label="Text to translate"
          rows={6}
          style={{ width: '100%' }}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <button type="submit" disabled={loading}>
          {loading ? 'Translating…' : 'Translate'}
        </button>
      </form>
      {result !== null && (
        <div>
          <h3>Result</h3>
          <pre data-testid="translate-result" style={{ whiteSpace: 'pre-wrap' }}>
            {result}
          </pre>
        </div>
      )}
      <h3>History</h3>
      {history.length === 0 ? (
        <p className="muted">No translations yet.</p>
      ) : (
        <ul data-testid="translate-history">
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
