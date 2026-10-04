import { useEffect, useRef, useState } from 'react'

import { updateDramaMetadata } from '../../../api/library'
import { analyzeMedia, applyMetadata, listPlatforms, suggestMetadata } from '../../../api/metadata'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { humanize } from '../../../components/labels'
import { Section } from '../../../components/Section'
import { buttonClass } from '../../../components/uiClasses'
import { MEDIA_TYPES } from '../../libraryForm'
import { usePersistedState, writePref } from '../../../hooks/usePersistedState'
import { writeSectionOpen } from '../../../components/sectionStorage'
import { wantsAutofill, withoutAutofill } from '../../libraryParity/libraryParity'
import type { KnownPlatform, MediaAnalysis } from '../../../types/workspace'
import {
  acceptedFields,
  analysisDetails,
  analysisSummary,
  autofillRequest,
  contentTypeSuggestion,
  defaultSelection,
  pipelineSteps,
  suggestionRows,
  type SuggestionRow,
} from '../metadataForm'
import { useStage } from '../StageContext'
import { ResearchPanel } from './ResearchPanel'
import './preamble.css'

// "Known official platforms" (inventory P04): where a listing page usually
// lives. Loaded the first time it is opened.
function KnownPlatforms() {
  const [items, setItems] = useState<KnownPlatform[] | null>(null)
  const [failed, setFailed] = useState(false)
  const load = (open: boolean) => {
    if (!open || items) return
    listPlatforms().then(setItems, () => setFailed(true))
  }
  return (
    <details className="known-platforms" onToggle={(e) => load((e.currentTarget as HTMLDetailsElement).open)}>
      <summary>Known official platforms</summary>
      {failed && <p className="muted">Couldn't load the list.</p>}
      {!failed && !items && <p className="muted">Loading…</p>}
      {items && (
        <ul aria-label="Known official platforms">
          {items.map((p) => (
            <li key={p.url}>
              <a href={p.url} target="_blank" rel="noopener noreferrer">{p.name}</a>
              {p.content_types?.length ? (
                <span className="muted"> · {p.content_types.map((t) => humanize('mediaType', t)).join(', ')}</span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </details>
  )
}

function AutofillBody({ arrived }: { arrived: boolean }) {
  const { dramaId, drama, refetchDrama } = useStage()
  const [url, setUrl] = useState('')
  const [text, setText] = useState('')
  const [rows, setRows] = useState<SuggestionRow[] | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const urlInput = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (!arrived) return
    window.history.replaceState(null, '', withoutAutofill(window.location.hash))
    urlInput.current?.scrollIntoView({ block: 'center' })
    urlInput.current?.focus()
  }, [arrived])

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
        <div className="source-panel">
          <Field label="Listing URL" help="A public http(s) page. Nothing is saved until you accept the suggestions.">
            <input ref={urlInput} type="url" value={url} onChange={(e) => setUrl(e.target.value)} />
          </Field>
          <KnownPlatforms />
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
  )
}

function AnalyzeBody({ hasMedia, onNeedMedia }: { hasMedia: boolean; onNeedMedia?: () => void }) {
  const { dramaId, drama, refetchDrama } = useStage()
  const [result, setResult] = useState<MediaAnalysis | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [applying, setApplying] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  const suggested = result ? contentTypeSuggestion(result, drama.media_type ?? null, MEDIA_TYPES) : null
  // Parity P05: "Use this content type" sets the drama's media type, nothing else.
  const applySuggestion = (mediaType: string) => {
    setApplying(true)
    updateDramaMetadata(dramaId, { media_type: mediaType }).then(
      () => {
        setApplying(false)
        setError(null)
        setNotice(`Media type set to ${humanize('mediaType', mediaType)}.`)
        refetchDrama()
      },
      (e: unknown) => {
        setApplying(false)
        setError(e)
      },
    )
  }

  const run = () => {
    setBusy(true)
    analyzeMedia(dramaId).then(
      (r) => {
        setError(null)
        setNotice(null)
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
        <div className="source-panel">
          {result && <p className="muted">{analysisSummary(result)}</p>}
          {hasMedia ? (
            <div>
              <button type="button" disabled={busy} onClick={run}>
                Analyze media
              </button>
            </div>
          ) : (
            <p className="muted source-needed">
              <span>Still needed: an audio or video file.</span>
              {onNeedMedia && (
                <button type="button" className={buttonClass('ghost', 'sm')} onClick={onNeedMedia}>
                  Choose a file
                </button>
              )}
            </p>
          )}
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
          {result?.content_type_guess && (
            <div className="source-suggestion" data-testid="analysis-suggestion">
              <p>
                Likely content type: <strong>{humanize('mediaType', result.content_type_guess)}</strong>
                {result.content_type_reason ? ` (${result.content_type_reason})` : ''}.
              </p>
              {suggested ? (
                <button type="button" className={buttonClass('secondary', 'sm')} disabled={applying} onClick={() => applySuggestion(suggested)}>
                  Use this content type
                </button>
              ) : (
                result.content_type_guess === drama.media_type && <p className="muted">This drama already uses it.</p>
              )}
            </div>
          )}
          {result && (result.suggested_pipeline ?? []).length > 0 && (
            <div data-testid="analysis-pipeline">
              <p className="muted">Suggested steps (nothing runs until you start it):</p>
              <ol className="source-pipeline">
                {pipelineSteps(result).map((step) => <li key={step}>{step}</li>)}
              </ol>
            </div>
          )}
          {notice && <p role="status">{notice}</p>}
          <ErrorBanner error={error} onDismiss={() => setError(null)} />
        </div>
  )
}

type FillMode = 'page' | 'research' | 'audio'
const FILL_MODES: [FillMode, string][] = [
  ['page', 'From a page or text'],
  ['research', 'Research online'],
  ['audio', 'From the audio'],
]

/**
 * One "suggest, then apply" fold for the three ways to fill in details: a
 * listing page or pasted text, Gemini research, or analyzing the attached
 * media. Every mode stays mounted so a pending suggestion survives switching.
 */
export function FillInPanel({ hasMedia, onNeedMedia }: { hasMedia: boolean; onNeedMedia?: () => void }) {
  // Arriving from Library "Create and auto-fill" (?autofill=1) opens this fold
  // on the page mode before the Section and the mode read their remembered state.
  const [arrived] = useState(() => {
    const want = wantsAutofill(window.location.hash)
    if (want) {
      try {
        writeSectionOpen(window.localStorage, 'source.fillin', true)
        writePref(window.localStorage, 'source.fillin.mode', 'page')
      } catch {
        // storage unavailable: defaultOpen still opens the fold
      }
    }
    return want
  })
  const [mode, setMode] = usePersistedState<FillMode>('source.fillin.mode', 'page')
  const { dramaId } = useStage()
  return (
    <section className="panel" aria-label="Fill in details">
      <Section storageKey="source.fillin" defaultOpen={arrived} title="Fill in details" summary="From a page, online research or the audio">
        <div className="source-panel">
          <div className="segmented fill-in-modes" role="radiogroup" aria-label="Fill in from">
            {FILL_MODES.map(([m, label]) => (
              <label key={m} className={m === mode ? 'segmented-on' : undefined}>
                <input type="radio" name={`fill-in-${dramaId}`} checked={m === mode} onChange={() => setMode(m)} />
                {label}
              </label>
            ))}
          </div>
          <div hidden={mode !== 'page'}>
            <AutofillBody arrived={arrived} />
          </div>
          <div hidden={mode !== 'research'}>
            <ResearchPanel />
          </div>
          <div hidden={mode !== 'audio'}>
            <AnalyzeBody hasMedia={hasMedia} onNeedMedia={onNeedMedia} />
          </div>
        </div>
      </Section>
    </section>
  )
}
