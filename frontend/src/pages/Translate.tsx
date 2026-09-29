import { useCallback, useEffect, useState } from 'react'

import { ApiError } from '../api/client'
import {
  NON_ENGLISH_LANGUAGES,
  engineShortName,
  engineSummary,
  languagePair,
  translateApi,
  validateTranslateInput,
} from '../api/translate'
import { Card } from '../components/Card'
import { ConfirmButton } from '../components/ConfirmButton'
import { ErrorBanner } from '../components/ErrorBanner'
import { copyText } from '../components/errorFallbackText'
import { humanize } from '../components/labels'
import { buttonClass } from '../components/uiClasses'
import { usePcOnly } from '../hooks/usePcOnly'
import { usePersistedState } from '../hooks/usePersistedState'
import { DownloadResultButton, OpenFileField } from './TranslateFileControls'
import {
  HISTORY_PREVIEW,
  engineOptionLabel,
  historyLabel,
  historyTime,
  isDirection,
  orderEngines,
  pickEngine,
  pickLanguage,
  pickModel,
  swapDirection,
  visibleHistory,
} from './translatePage'
import './translate.css'
import type { TranslateEngine, TranslateHistoryEntry } from '../types/translate'

export default function TranslatePage() {
  const [engines, setEngines] = useState<TranslateEngine[]>([])
  const [history, setHistory] = useState<TranslateHistoryEntry[]>([])
  // Remembered per viewer (react-ui-guidelines rule 12); validated on read.
  const [enginePref, setEnginePref] = usePersistedState('translate.engine', '')
  const [modelPref, setModelPref] = usePersistedState('translate.model', '')
  const [directionPref, setDirectionPref] = usePersistedState('translate.direction', 'to_english')
  const [otherPref, setOtherPref] = usePersistedState('translate.language', 'zh')
  const [text, setText] = useState('')
  const [result, setResult] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(false)
  const [sourceName, setSourceName] = useState<string | null>(null)
  const [fileMessage, setFileMessage] = useState<string | null>(null)
  const [resultTarget, setResultTarget] = useState('en')
  const [copied, setCopied] = useState<'ok' | 'failed' | null>(null)
  const [showAll, setShowAll] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [clearError, setClearError] = useState<unknown>(null)
  const pc = usePcOnly()

  const refreshHistory = useCallback(() => translateApi.history().then(setHistory, setError), [])

  useEffect(() => {
    translateApi.engines().then(setEngines, setError)
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

  const engine = pickEngine(engines, enginePref)
  const selected = engines.find((e) => e.name === engine)
  const model = pickModel(selected, modelPref)
  const direction = isDirection(directionPref) ? directionPref : 'to_english'
  const other = pickLanguage(otherPref)
  const pair = languagePair(direction, other)
  const sourceLabel = humanize('language', pair.source_language)
  const noKey = selected !== undefined && !selected.key_configured

  const setResultText = (value: string | null) => {
    setResult(value)
    setCopied(null)
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    const invalid = validateTranslateInput(text, engine)
    if (invalid) {
      setError(new ApiError(422, { code: 'validation_error', message: invalid }))
      return
    }
    setError(null)
    setResultText(null)
    setLoading(true)
    try {
      setResultText(
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

  const clearInput = () => {
    setText('')
    setResultText(null)
    setSourceName(null)
    setFileMessage(null)
    setError(null)
  }

  const copyResult = () => {
    if (result === null) return
    void copyText(result).then((ok) => setCopied(ok ? 'ok' : 'failed'))
  }

  const languageSelect = (label: string) => (
    <select aria-label={label} value={other} onChange={(e) => setOtherPref(e.target.value)}>
      {NON_ENGLISH_LANGUAGES.map((l) => (
        <option key={l.code} value={l.code}>
          {l.label}
        </option>
      ))}
    </select>
  )
  const english = <span className="translate-lang-fixed">English</span>

  const shown = visibleHistory(history, showAll)

  return (
    <section className="translate-page" aria-label="Translate">
      <header className="translate-head">
        <h2>Translate</h2>
        <p className="muted">Quick text translation, outside any drama. One side is always English.</p>
      </header>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <Card className="translate-card" as="div">
        <form onSubmit={submit} className="translate-form">
          <div className={selected?.models ? 'translate-toolbar has-model' : 'translate-toolbar'}>
            <label className="field translate-engine">
              <span>Engine</span>
              <select
                aria-label="Engine"
                value={engine}
                onChange={(e) => {
                  setEnginePref(e.target.value)
                  setModelPref('')
                }}
              >
                {orderEngines(engines).map((en) => (
                  <option key={en.name} value={en.name}>
                    {engineOptionLabel(en)}
                  </option>
                ))}
              </select>
            </label>
            {selected?.models && (
              <label className="field translate-model">
                <span>Model</span>
                <select value={model} onChange={(e) => setModelPref(e.target.value)}>
                  <option value="">Default</option>
                  {selected.models.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <div className="field translate-langs" role="group" aria-labelledby="translate-langs-label">
              <span id="translate-langs-label">Languages</span>
              <div className="translate-lang-row">
                {direction === 'to_english' ? languageSelect('Source language') : english}
                <button
                  type="button"
                  className={buttonClass('secondary', 'md', 'translate-swap')}
                  aria-label="Swap languages"
                  onClick={() => setDirectionPref(swapDirection(direction))}
                >
                  <span aria-hidden="true">⇄</span>
                </button>
                {direction === 'to_english' ? english : languageSelect('Target language')}
              </div>
            </div>
          </div>
          {selected && (
            <p className="muted translate-engine-note" data-testid="engine-note">
              {engineShortName(selected)}: {engineSummary(selected.label)}
            </p>
          )}

          <div className="translate-panes">
            <div className="translate-pane translate-source">
              <div className="translate-pane-head">
                <label htmlFor="translate-text" className="translate-pane-title">
                  Text to translate <span className="muted">· {sourceLabel}</span>
                </label>
                <OpenFileField
                  sourceName={sourceName}
                  message={fileMessage}
                  target={{ setText, setSourceName, setFileMessage }}
                />
              </div>
              <textarea
                id="translate-text"
                rows={8}
                value={text}
                onChange={(e) => setText(e.target.value)}
              />
              <div className="actions translate-actions">
                <button
                  type="submit"
                  className={buttonClass('primary')}
                  disabled={loading || noKey}
                  aria-describedby={noKey ? 'translate-needs-key' : undefined}
                >
                  {loading ? 'Translating…' : 'Translate'}
                </button>
                <button
                  type="button"
                  className={buttonClass('ghost')}
                  onClick={clearInput}
                  disabled={loading || (!text && result === null && !sourceName && !fileMessage)}
                >
                  Clear
                </button>
              </div>
              {noKey && selected && (
                <p id="translate-needs-key" className="translate-warning">
                  Still needed: an API key for {engineShortName(selected)}.{' '}
                  <a href="#/settings">Add it in Settings</a>
                </p>
              )}
            </div>

            <div
              className="translate-pane translate-result-pane"
              role="region"
              aria-label="Result"
              aria-busy={loading || undefined}
            >
              <div className="translate-pane-head">
                <h3 className="translate-pane-title">{humanize('language', result !== null ? resultTarget : pair.target_language)}</h3>
                {result !== null && (
                  <div className="translate-result-actions">
                    <button type="button" className={buttonClass('ghost', 'sm')} onClick={copyResult}>
                      {copied === 'ok' ? 'Copied' : 'Copy'}
                    </button>
                    <DownloadResultButton result={result} sourceName={sourceName} targetLanguage={resultTarget} />
                  </div>
                )}
              </div>
              {result !== null ? (
                <pre data-testid="translate-result" className="translate-result">
                  {result}
                </pre>
              ) : (
                <p className="muted translate-placeholder">
                  {loading ? 'Translating…' : 'The translation appears here.'}
                </p>
              )}
              {copied === 'failed' && (
                <p className="translate-warning" role="status">
                  Couldn't copy. Select the text and copy it instead.
                </p>
              )}
            </div>
          </div>
        </form>
      </Card>

      <Card
        className="translate-history-card"
        title="History"
        meta={history.length > 0 ? `${history.length} saved, newest first` : undefined}
        actions={
          history.length > 0 &&
          (pc === 'remote' ? (
            <span className="muted">Clearing history is PC only.</span>
          ) : (
            <ConfirmButton
              name="translation history"
              label="Clear history…"
              ariaLabel="Clear history"
              verb="clear"
              busy={clearing}
              onConfirm={clearHistory}
            />
          ))
        }
      >
        <ErrorBanner error={clearError} describe={{ pcOnly: true }} onDismiss={() => setClearError(null)} />
        {history.length === 0 ? (
          <p className="muted">No translations yet.</p>
        ) : (
          <>
            <ul data-testid="translate-history" className="translate-history">
              {shown.map((h, i) => (
                <li key={`${h.created_at}-${i}`}>
                  <div className="translate-history-meta">
                    <span>{historyLabel(h)}</span>
                    {h.created_at && <span className="muted">{historyTime(h.created_at)}</span>}
                  </div>
                  <div className="translate-history-pair">
                    <p>{h.source_text}</p>
                    <p>{h.translated_text}</p>
                  </div>
                </li>
              ))}
            </ul>
            {history.length > HISTORY_PREVIEW && (
              <div className="actions">
                <button
                  type="button"
                  className={buttonClass('ghost')}
                  aria-expanded={showAll}
                  onClick={() => setShowAll(!showAll)}
                >
                  {showAll ? 'Show fewer' : `Show all (${history.length})`}
                </button>
              </div>
            )}
          </>
        )}
      </Card>
    </section>
  )
}
