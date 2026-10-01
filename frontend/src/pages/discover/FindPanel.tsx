/*
 * Discover > Find on official platforms (DI04) and the known-platforms list.
 * The links are built on the server; nothing is searched from inside the
 * app. An English query is translated to Chinese first (one AI call, only
 * when the button is pressed; cached per query and engine for this visit).
 */
import { useEffect, useRef, useState, type FormEvent } from 'react'

import { listPlatforms, searchLinks, translateQuery } from '../../api/discover'
import { ExternalLink } from './ExternalLink'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Section } from '../../components/Section'
import { Toggle } from '../../components/Toggle'
import { buttonClass } from '../../components/uiClasses'
import type { Platform, SearchGenre, SearchLink } from '../../types/discover'
import { LANGUAGES, LINK_FORMATS, PLATFORM_TYPES, hasChinese, mediaLabel } from './discoverFormat'

export function FindPanel({ engine, canTranslate }: { engine: string; canTranslate: boolean }) {
  const [q, setQ] = useState('')
  const [format, setFormat] = useState('')
  const [genre, setGenre] = useState<SearchGenre>('baihe')
  const [tag, setTag] = useState('')
  const [translate, setTranslate] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [note, setNote] = useState<string | null>(null)
  const [links, setLinks] = useState<SearchLink[] | null>(null)
  // Loaded on first open only.
  const [showPlatforms, setShowPlatforms] = useState(false)
  const cache = useRef(new Map<string, string>())

  async function find(e: FormEvent) {
    e.preventDefault()
    const text = q.trim()
    if (!text || busy) return
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      let zh = text
      if (translate && canTranslate && !hasChinese(text)) {
        const key = `${engine}\u0000${text}`
        const hit = cache.current.get(key)
        if (hit !== undefined) {
          zh = hit
        } else {
          try {
            zh = (await translateQuery(text, engine || undefined)).translated || text
            cache.current.set(key, zh)
          } catch {
            setNote("Couldn't translate the query, so these search with your text as typed.")
          }
        }
        if (zh !== text) setNote(`Searching Chinese platforms for: ${zh}`)
      }
      setLinks(await searchLinks(zh, format, genre, tag.trim()))
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="discover-block">
      <p className="muted discover-lead">
        Builds real search links across the known platforms and hands them to you, so nothing can be invented. Open the
        ones that look right, then add what you find with “Add a title”.
      </p>
      <form className="discover-row" onSubmit={find}>
        <Field label="Title to find">
          <input
            type="search"
            value={q}
            maxLength={200}
            onChange={(e) => setQ(e.target.value)}
            placeholder="女将军和长公主 or The General and the Princess"
          />
        </Field>
        <Field label="Format">
          <select value={format} onChange={(e) => setFormat(e.target.value)}>
            <option value="">Any</option>
            {LINK_FORMATS.map((f) => (
              <option key={f} value={f}>
                {mediaLabel(f)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Genre">
          <select value={genre} onChange={(e) => setGenre(e.target.value as SearchGenre)}>
            <option value="baihe">Baihe only</option>
            <option value="any">All genres</option>
          </select>
        </Field>
        {genre === 'any' && (
          <Field label="JJWXC tag" help="Optional. Leave blank for JJWXC's general listing.">
            <input type="text" value={tag} maxLength={40} onChange={(e) => setTag(e.target.value)} placeholder="言情" />
          </Field>
        )}
        <button type="submit" className={buttonClass('primary')} disabled={!q.trim() || busy} aria-busy={busy}>
          {busy ? 'Finding…' : 'Find'}
        </button>
      </form>
      {canTranslate ? (
        <div className="setting-list">
          <Field label="Translate English to Chinese first" help="One AI call with the engine above, only when you press Find.">
            <Toggle checked={translate} onChange={setTranslate} />
          </Field>
        </div>
      ) : (
        <p className="muted">No AI engine is set up, so the title is searched as typed.</p>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {note && (
        <p className="muted" role="status">
          {note}
        </p>
      )}
      {links && (
        <ul className="discover-links" data-testid="search-links">
          {links.length === 0 && <li className="muted">No platform matches that format.</li>}
          {links.map((l) => (
            <li key={l.url}>
              <ExternalLink href={l.url}>
                {l.site}
              </ExternalLink>
              {l.note && <span className="muted"> — {l.note}</span>}
            </li>
          ))}
        </ul>
      )}
      <Section title="Known official platforms" summary="Chinese, Korean and Japanese sites" onToggle={(open) => open && setShowPlatforms(true)}>
        {showPlatforms && <PlatformList />}
      </Section>
    </div>
  )
}

function PlatformList() {
  const [language, setLanguage] = useState('')
  const [type, setType] = useState('')
  const [platforms, setPlatforms] = useState<Platform[] | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let live = true
    listPlatforms(language, type).then(
      (p) => live && (setPlatforms(p), setError(null)),
      (e) => live && setError(e),
    )
    return () => {
      live = false
    }
  }, [language, type])

  return (
    <div className="discover-block">
      <div className="discover-filters">
        <Field label="Platform language">
          <select value={language} onChange={(e) => setLanguage(e.target.value)}>
            <option value="">All</option>
            {LANGUAGES.map((l) => (
              <option key={l.code} value={l.code}>
                {l.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Content type">
          <select value={type} onChange={(e) => setType(e.target.value)}>
            <option value="">All</option>
            {PLATFORM_TYPES.map((t) => (
              <option key={t} value={t}>
                {mediaLabel(t)}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <ErrorBanner error={error} />
      {platforms && (
        <ul className="discover-links" data-testid="platforms">
          {platforms.length === 0 && <li className="muted">No platform matches.</li>}
          {platforms.map((p) => (
            <li key={p.url}>
              <ExternalLink href={p.url}>
                {p.name}
              </ExternalLink>
              <span className="muted">
                {' '}
                — {[p.region, (p.content_types ?? []).map(mediaLabel).join(', ')].filter(Boolean).join(' · ')}
              </span>
              {p.notes && <div className="muted">{p.notes}</div>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
