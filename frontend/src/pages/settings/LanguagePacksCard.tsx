/*
 * Settings > Language packs: the built-in glossaries (honorifics, address terms, kinship, set
 * phrases) per source language, and which are on by default. Read-only; each title chooses its
 * own packs on its Glossary page.
 */
import { useEffect, useState } from 'react'

import { getLanguagePack, getLanguagePackDefault, listLanguagePacks } from '../../api/languagePacks'
import { Card } from '../../components/Card'
import { ErrorBanner } from '../../components/ErrorBanner'
import type { LanguagePack, LanguagePackDefaultResult, LanguagePackSummary } from '../../types/languagePacks'
import { entryText, groupPacks, PACK_LANGUAGES } from './languagePacksModel'

export function LanguagePacksCard() {
  const [packs, setPacks] = useState<LanguagePackSummary[] | null>(null)
  const [defaults, setDefaults] = useState<Record<string, LanguagePackDefaultResult>>({})
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    const langs = PACK_LANGUAGES.filter((l) => l.id !== 'any').map((l) => l.id)
    Promise.all([listLanguagePacks(), ...langs.map((l) => getLanguagePackDefault(l))]).then(
      ([list, ...found]) => {
        setPacks(list)
        setDefaults(Object.fromEntries(found.map((d) => [d.language, d])))
      },
      setError,
    )
  }, [])

  const groups = packs ? groupPacks(packs, defaults) : []
  return (
    <Card title="Language packs" meta="Built-in glossaries" aria-label="Language packs">
      <p className="muted">
        Language packs are ready-made lists of honorifics, address terms, family words and set phrases that
        help the translation stay consistent. A title uses a pack only when it is turned on for that title; the
        packs marked “on by default” are what titles of that language follow until they get their own choice.
        A title’s own glossary always beats a pack entry.
      </p>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {packs === null && !error && <p className="muted">Loading…</p>}
      {groups.map((g) => (
        <section key={g.id} aria-label={g.label}>
          <h2>{g.label}</h2>
          <ul className="setting-list pack-list">
            {g.packs.map(({ pack, onByDefault }) => (
              <PackRow key={pack.id} pack={pack} onByDefault={onByDefault} />
            ))}
          </ul>
        </section>
      ))}
    </Card>
  )
}

function PackRow({ pack, onByDefault }: { pack: LanguagePackSummary; onByDefault: boolean | null }) {
  const [full, setFull] = useState<LanguagePack | null>(null)
  const [error, setError] = useState<unknown>(null)
  const status = onByDefault === null ? '' : onByDefault ? ' · on by default' : ' · off by default'
  return (
    <li>
      <details
        onToggle={(e) => {
          if ((e.currentTarget as HTMLDetailsElement).open && !full) {
            getLanguagePack(pack.id).then(setFull, setError)
          }
        }}
      >
        <summary>
          <span className="pack-title">{pack.title}</span>
          <span className="muted pack-meta">{pack.entry_count} entries{status}</span>
        </summary>
        {pack.description && <p className="muted">{pack.description}</p>}
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
        {full && (
          <table>
            <thead>
              <tr><th>Source</th><th>English</th><th>Note</th></tr>
            </thead>
            <tbody>
              {full.entries.map((en, i) => (
                <tr key={i}>
                  <td>{en.source}</td>
                  <td>{entryText(en.en, full.styles.default)}</td>
                  <td>{en.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </details>
    </li>
  )
}
