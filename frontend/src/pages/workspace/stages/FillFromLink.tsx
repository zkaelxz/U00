import { useState } from 'react'

import { probeLink, probeTitleLink, type LinkDetailFields, type MediaLinkProbe } from '../../../api/linkDetails'
import { suggestMetadata } from '../../../api/metadata'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { buttonClass } from '../../../components/uiClasses'
import { usePcOnly } from '../../../hooks/usePcOnly'
import { defaultTicks, linkRows, linkValues, tickedValues, type LinkField, type LinkRow } from '../../linkFill'

/**
 * Media > Details "Fill from link": reads the title's link (yt-dlp for a video,
 * the source's own data for a Sources link; free) and offers its title,
 * channel and stream date. Only the draft form changes; Save writes it.
 */
export function FillFromLink({ dramaId, link, savedLink, current, onUse }: {
  dramaId: number
  link: string // the link in the form now
  savedLink: string // the link as saved (the server reads its full form)
  current: Pick<LinkDetailFields, 'title_zh' | 'author' | 'release_date'>
  onUse: (values: Partial<LinkDetailFields>) => void
}) {
  const pc = usePcOnly()
  const [probe, setProbe] = useState<MediaLinkProbe | null>(null)
  const [aiValues, setAiValues] = useState<Partial<LinkDetailFields> | null>(null)
  const [ticked, setTicked] = useState<Set<LinkField>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const url = link.trim()
  if (pc === 'remote') return null

  const values = aiValues ?? (probe ? linkValues(probe) : null)
  const rows: LinkRow[] = values ? linkRows(values, current) : []

  const read = () => {
    setBusy(true)
    setError(null)
    setAiValues(null)
    const asked = url === savedLink.trim() ? probeTitleLink(dramaId).then((r) => r.link) : probeLink(url)
    asked.then(
      (p) => {
        setBusy(false)
        setProbe(p)
        setTicked(defaultTicks(linkRows(linkValues(p), current)))
      },
      (e: unknown) => {
        setBusy(false)
        setError(e)
      },
    )
  }

  // The second, explicit press for any other page: the paid AI listing read.
  const readWithAi = () => {
    setBusy(true)
    suggestMetadata(dramaId, { url }).then(
      (r) => {
        setBusy(false)
        const next = { title_zh: r.suggestion.title_zh ?? '', author: r.suggestion.author ?? '' }
        setAiValues(next)
        setTicked(defaultTicks(linkRows(next, current)))
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
  const fill = tickedValues(rows, ticked)

  return (
    <div className="source-panel" data-testid="fill-from-link">
      <div className="actions">
        <button type="button" className={buttonClass('secondary', 'sm')} disabled={!url || busy} onClick={read}>
          Fill from link
        </button>
        {!url && <span className="muted">Still needed: a source link.</span>}
        {busy && <span className="muted">Reading the link…</span>}
      </div>
      {probe?.kind === 'page' && !aiValues && (
        <p className="muted">
          {probe.note}{' '}
          <button type="button" className={buttonClass('ghost', 'sm')} disabled={busy} onClick={readWithAi}>
            Read the page with AI (may cost money)
          </button>
        </p>
      )}
      {values && probe?.kind !== 'page' && probe?.note && <p className="muted">{probe.note}</p>}
      {values && (probe?.kind !== 'page' || aiValues) && !rows.length && <p role="status">The link gave nothing to fill.</p>}
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
                  <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => onUse({ [r.field]: r.value })}>
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
          <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => onUse(fill)}>
            Fill ticked fields
          </button>
          <span className="muted">Then Save details.</span>
        </div>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </div>
  )
}
