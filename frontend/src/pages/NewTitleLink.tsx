import { useState } from 'react'

import { probeLink, type MediaLinkProbe } from '../api/linkDetails'
import { engineShortName, languagePair, translateApi } from '../api/translate'
import { ErrorBanner } from '../components/ErrorBanner'
import { Field } from '../components/Field'
import { buttonClass } from '../components/uiClasses'
import { usePcOnly } from '../hooks/usePcOnly'
import type { DramaCreateRequest } from '../types/library'
import type { TranslateRequest } from '../types/translate'
import { MAX_URL_LEN } from './libraryForm'
import { defaultTicks, englishTitleCost, linkRows, linkValues, tickedValues, type LinkField } from './linkFill'
import { pickEngine } from './translatePage'

/**
 * New title: "Link (optional)" with Fill from link (the free probe; PC only),
 * which fills an empty original title and stream date and only suggests the
 * channel as the series. "Suggest English title" translates the original
 * title on the configured engine, only when pressed, with its cost shown first.
 */
export function NewTitleLink({ form, onChange, onSeries }: {
  form: DramaCreateRequest
  onChange: (values: Partial<DramaCreateRequest>) => void
  onSeries: ((name: string) => void) | null // null once a series is chosen
}) {
  const pc = usePcOnly()
  const [probe, setProbe] = useState<MediaLinkProbe | null>(null)
  const [ticked, setTicked] = useState<Set<LinkField>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [pending, setPending] = useState<{ req: TranslateRequest; note: string; runs: boolean } | null>(null)
  const url = (form.source_url ?? '').trim()
  const current = { title_zh: form.title_zh ?? '', release_date: form.release_date ?? '' }
  const rows = probe ? linkRows(linkValues(probe), current, ['title_zh', 'release_date']) : []

  const read = () => {
    setBusy(true)
    setError(null)
    probeLink(url).then(
      (p) => {
        setBusy(false)
        setProbe(p)
        setTicked(defaultTicks(linkRows(linkValues(p), current, ['title_zh', 'release_date'])))
      },
      (e: unknown) => {
        setBusy(false)
        setError(e)
      },
    )
  }
  const toggle = (field: LinkField) =>
    setTicked((s) => {
      const n = new Set(s)
      if (!n.delete(field)) n.add(field)
      return n
    })

  const translate = (req: TranslateRequest) => {
    setBusy(true)
    setPending(null)
    translateApi.translate(req).then(
      (text) => {
        setBusy(false)
        if (!(form.title_en ?? '').trim()) onChange({ title_en: text.trim().slice(0, 300) })
      },
      (e: unknown) => {
        setBusy(false)
        setError(e)
      },
    )
  }
  // Never automatic: a free engine runs on this press; a paid one shows its price and waits for a second press.
  const suggestEnglish = () => {
    setBusy(true)
    setError(null)
    translateApi.engineList().then(
      (list) => {
        const engine = list.items.find((e) => e.name === pickEngine(list.items, '', list.default_engine))
        if (!engine) {
          setBusy(false)
          setPending({ req: {} as TranslateRequest, note: 'No translator is set up yet.', runs: false })
          return
        }
        const req = { text: (form.title_zh ?? '').trim(), engine: engine.name, model: null, ...languagePair('to_english', form.source_language) }
        if (engine.free) return translate(req)
        translateApi.estimate(req).then(
          (est) => {
            setBusy(false)
            setPending({ req, note: englishTitleCost(engineShortName(engine), est), runs: est.fits_cap })
          },
          (e: unknown) => {
            setBusy(false)
            setError(e)
          },
        )
      },
      (e: unknown) => {
        setBusy(false)
        setError(e)
      },
    )
  }

  const fill = tickedValues(rows, ticked)
  const suggestedSeries = probe?.series_suggestion ?? ''
  const canSuggestEnglish = !!(form.title_zh ?? '').trim() && !(form.title_en ?? '').trim()

  return (
    <div className="stack" data-testid="new-title-link">
      <Field label="Link (optional)" help="A video, stream or Sources page. With a link, the name can stay empty: the title is named from the link.">
        <input type="url" value={form.source_url ?? ''} maxLength={MAX_URL_LEN} onChange={(e) => { setProbe(null); onChange({ source_url: e.target.value }) }} />
      </Field>
      {pc !== 'remote' && (
        <div className="actions">
          <button type="button" className={buttonClass('secondary', 'sm')} disabled={!url || busy} onClick={read}>
            Fill from link
          </button>
          {busy && <span className="muted">Working…</span>}
        </div>
      )}
      {probe?.kind === 'page' && (
        <p className="muted">{probe.note} "Create and auto-fill" can read it with AI after the title is made.</p>
      )}
      {probe && probe.kind !== 'page' && !rows.length && !suggestedSeries && <p role="status">The link gave nothing to fill.</p>}
      {rows.length > 0 && (
        <ul className="source-suggestions" aria-label="What the link says">
          {rows.map((r) => (
            <li key={r.field}>
              {r.mode === 'fill' && (
                <label className="check">
                  <input type="checkbox" checked={ticked.has(r.field)} onChange={() => toggle(r.field)} />
                  <span><strong>{r.label}:</strong> {r.value}</span>
                </label>
              )}
              {r.mode === 'differs' && (
                <span>
                  <strong>{r.label}:</strong> {r.current}. <span className="muted">Link says: {r.value}</span>{' '}
                  <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => onChange({ [r.field]: r.value })}>
                    Use this
                  </button>
                </span>
              )}
              {r.mode === 'same' && <span><strong>{r.label}:</strong> {r.value} <span className="muted">(already the same)</span></span>}
            </li>
          ))}
        </ul>
      )}
      {Object.keys(fill).length > 0 && (
        <div className="actions">
          <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => onChange(fill)}>
            Fill ticked fields
          </button>
        </div>
      )}
      {suggestedSeries && onSeries && (
        <p className="muted">
          Suggested series (folder): <strong>{suggestedSeries}</strong>{' '}
          <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => onSeries(suggestedSeries)}>
            Use as new series
          </button>
        </p>
      )}
      {canSuggestEnglish && (
        <div className="actions">
          <button type="button" className={buttonClass('ghost', 'sm')} disabled={busy} onClick={suggestEnglish}>
            Suggest English title
          </button>
        </div>
      )}
      {pending && (
        <p className="muted" data-testid="english-title-cost">
          {pending.note}{' '}
          {pending.runs && (
            <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy} onClick={() => translate(pending.req)}>
              Translate the title
            </button>
          )}
        </p>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </div>
  )
}
