/*
 * Discover > Catalogue (DI02, DI03): search the saved known titles, filter
 * by language and format, open a title's details, add it to the Library as
 * a drama, remove it (PC only). An empty catalogue offers the starter titles.
 */
import { useEffect, useState } from 'react'

import { deleteTitle, importToLibrary, listTitles, seedTitles } from '../../api/discover'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ExternalLink } from './ExternalLink'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { PC_ONLY_DELETE_NOTE, type PcMode } from '../../hooks/usePcOnly'
import { routeHref } from '../../router'
import type { KnownTitle, KnownTitleList } from '../../types/discover'
import { LANGUAGES, TITLE_MEDIA_TYPES, catalogCount, existingDramaId, mediaLabel, sourceLabel, titleMeta } from './discoverFormat'

// title id -> drama id once added (or found already added) this visit
type Imported = Record<number, number>

export function CatalogPanel({ pc, reloadKey }: { pc: PcMode; reloadKey: number }) {
  const [q, setQ] = useState('')
  const [language, setLanguage] = useState('')
  const [mediaType, setMediaType] = useState('')
  const [data, setData] = useState<KnownTitleList | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [actionError, setActionError] = useState<{ error: unknown; pcOnly: boolean } | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [imported, setImported] = useState<Imported>({})
  const [notice, setNotice] = useState<string | null>(null)
  const [seedKey, setSeedKey] = useState(0)

  useEffect(() => {
    let live = true
    // Debounced while typing; filters apply at once.
    const t = setTimeout(() => {
      listTitles({ q: q.trim(), language, media_type: mediaType }).then(
        (r) => live && (setData(r), setLoadError(null)),
        (e) => live && setLoadError(e),
      )
    }, 250)
    return () => {
      live = false
      clearTimeout(t)
    }
  }, [q, language, mediaType, reloadKey, seedKey])

  async function run(key: string, fn: () => Promise<void>, pcOnly = false) {
    setBusy(key)
    setActionError(null)
    setNotice(null)
    try {
      await fn()
    } catch (e) {
      setActionError({ error: e, pcOnly })
    } finally {
      setBusy(null)
    }
  }

  const seed = () =>
    run('seed', async () => {
      const r = await seedTitles()
      setNotice(`Added ${r.added} starter title${r.added === 1 ? '' : 's'}.`)
      setSeedKey((k) => k + 1)
    })

  const addToLibrary = (t: KnownTitle) =>
    run(`import-${t.id}`, async () => {
      try {
        const d = await importToLibrary(t.id)
        setImported((m) => ({ ...m, [t.id]: d.id }))
        setNotice(`Added “${t.title_en || t.title_original}” to your Library.`)
      } catch (e) {
        const existing = existingDramaId(e)
        if (existing === null) throw e
        setImported((m) => ({ ...m, [t.id]: existing }))
        setNotice('That title is already in your Library.')
      }
    })

  const remove = (t: KnownTitle) =>
    run(`delete-${t.id}`, async () => {
      await deleteTitle(t.id)
      setData((d) => (d ? { titles: d.titles.filter((x) => x.id !== t.id), total: Math.max(0, d.total - 1) } : d))
    }, true)

  const empty = data?.total === 0

  return (
    <div className="discover-block">
      <p className="muted discover-lead">
        Titles you have saved: cataloguing info only (title, author, tags, a short synopsis), not the works. This
        searches your saved list, not the web.
      </p>
      <div className="discover-filters">
        <Field label="Search saved titles">
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)} maxLength={200} placeholder="Any language" />
        </Field>
        <Field label="Language">
          <select value={language} onChange={(e) => setLanguage(e.target.value)}>
            <option value="">All</option>
            {LANGUAGES.map((l) => (
              <option key={l.code} value={l.code}>
                {l.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Format">
          <select value={mediaType} onChange={(e) => setMediaType(e.target.value)}>
            <option value="">All</option>
            {TITLE_MEDIA_TYPES.map((m) => (
              <option key={m} value={m}>
                {mediaLabel(m)}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <ErrorBanner error={loadError} />
      <ErrorBanner error={actionError?.error} onDismiss={() => setActionError(null)} describe={{ pcOnly: actionError?.pcOnly }} />
      {notice && (
        <p className="discover-ok" role="status">
          {notice}
        </p>
      )}
      <p className="muted" data-testid="catalog-count" aria-live="polite">
        {data ? catalogCount(data.titles.length, data.total, q.trim()) : loadError ? null : 'Loading…'}
      </p>
      {empty && (
        <div className="discover-empty">
          <p className="muted">Load a few known baihe titles to start with, or add titles below.</p>
          <button type="button" className="primary" onClick={seed} disabled={busy === 'seed'} aria-busy={busy === 'seed'}>
            {busy === 'seed' ? 'Loading…' : 'Load starter titles'}
          </button>
        </div>
      )}
      {data && data.titles.length > 0 && (
        <ul className="discover-list" data-testid="catalog-list">
          {data.titles.map((t) => (
            <TitleCard
              key={t.id}
              t={t}
              pc={pc}
              dramaId={imported[t.id] ?? null}
              busy={busy}
              onAdd={() => addToLibrary(t)}
              onRemove={() => remove(t)}
            />
          ))}
        </ul>
      )}
    </div>
  )
}

function TitleCard({ t, pc, dramaId, busy, onAdd, onRemove }: {
  t: KnownTitle
  pc: PcMode
  dramaId: number | null
  busy: string | null
  onAdd: () => void
  onRemove: () => void
}) {
  const name = t.title_original || t.title_en || `Title ${t.id}`
  const adding = busy === `import-${t.id}`
  return (
    <li className="discover-card">
      <div className="discover-card-head">
        <strong lang={t.language || undefined}>{t.title_original}</strong>
        {t.title_en && <em>{t.title_en}</em>}
      </div>
      <p className="muted discover-card-meta">{titleMeta(t)}</p>
      {(t.summary_en || t.summary_original || t.source_url) && (
        <details className="discover-card-detail">
          <summary>Details</summary>
          {t.summary_en && <p>{t.summary_en}</p>}
          {t.summary_original && <p lang={t.language || undefined}>{t.summary_original}</p>}
          {t.source_url && (
            <p className="muted">
              Source:{' '}
              <ExternalLink href={t.source_url}>
                {sourceLabel(t.source_name, t.source_url)}
              </ExternalLink>
            </p>
          )}
        </details>
      )}
      <div className="discover-card-actions">
        {dramaId !== null ? (
          <a className="discover-in-library" href={routeHref({ name: 'drama', id: dramaId, stage: 'source' })}>
            In your Library — open
          </a>
        ) : (
          <button type="button" onClick={onAdd} disabled={adding} aria-busy={adding} aria-label={`Add ${name} to Library`}>
            {adding ? 'Adding…' : 'Add to Library'}
          </button>
        )}
        {pc === 'remote' ? (
          <span className="muted">{PC_ONLY_DELETE_NOTE}</span>
        ) : (
          <ConfirmButton name={name} label="Remove…" verb="remove" busy={busy === `delete-${t.id}`} onConfirm={onRemove} />
        )}
      </div>
    </li>
  )
}
