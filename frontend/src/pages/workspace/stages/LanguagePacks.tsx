import { useEffect, useState } from 'react'

import { getLanguagePack, getTitleLanguagePacks, setLanguagePackDefault, setTitleLanguagePacks } from '../../../api/languagePacks'
import { saveGlossaryTerm } from '../../../api/translateStage'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Section } from '../../../components/Section'
import { Toggle } from '../../../components/Toggle'
import { buttonClass } from '../../../components/uiClasses'
import type { LanguagePack, TitleLanguagePack, TitleLanguagePacks } from '../../../types/languagePacks'
import { useStage } from '../StageContext'
import { choiceAfter, entryRendering, PACK_LANGUAGE_NAMES, packsSummary, termFromEntry } from './languagePacksModel'
import './languagePacks.css'

function PackEntries({ pack, inGlossary, canAdd, onAdded, onError }: {
  pack: TitleLanguagePack
  inGlossary: Set<string>
  canAdd: boolean
  onAdded: () => void
  onError: (e: unknown) => void
}) {
  const { dramaId } = useStage()
  const [loaded, setLoaded] = useState<LanguagePack | null>(null)
  const [open, setOpen] = useState(false)
  const [added, setAdded] = useState<Set<string>>(new Set())

  useEffect(() => {
    if (!open || loaded) return
    let cancelled = false
    getLanguagePack(pack.id).then((p) => !cancelled && setLoaded(p), (e: unknown) => !cancelled && onError(e))
    return () => {
      cancelled = true
    }
  }, [open, loaded, pack.id, onError])

  return (
    <details className="lp-entries" onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary>View entries ({pack.entry_count})</summary>
      {loaded && (
        <ul className="lp-list">
          {loaded.entries.map((entry) => {
            const have = inGlossary.has(entry.source) || added.has(entry.source)
            return (
              <li key={entry.source} className="lp-entry">
                <div className="lp-entry-text">
                  <strong lang={pack.language === 'any' ? undefined : pack.language}>{entry.source}</strong>
                  <span aria-hidden="true"> → </span>
                  <span>{entryRendering(entry, pack)}</span>
                  {(entry.context || entry.note) && (
                    <div className="muted">{[entry.context, entry.note].filter(Boolean).join('. ')}</div>
                  )}
                </div>
                <button
                  type="button"
                  className={buttonClass('secondary', 'sm')}
                  disabled={have || !canAdd}
                  aria-label={`Add ${entry.source} to my glossary`}
                  onClick={() =>
                    saveGlossaryTerm(dramaId, termFromEntry(entry, pack)).then(
                      () => {
                        setAdded((s) => new Set(s).add(entry.source))
                        onAdded()
                      },
                      onError,
                    )
                  }
                >
                  {have ? 'In my glossary' : 'Add to my glossary'}
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </details>
  )
}

/** Built-in starter packs: honorifics, address terms and common terms, off until turned on for the title. */
export function LanguagePacks({ glossarySources, hasSeries, onAdded }: {
  glossarySources: string[]
  hasSeries: boolean
  onAdded: () => void
}) {
  const { dramaId } = useStage()
  const [data, setData] = useState<TitleLanguagePacks | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [saved, setSaved] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    getTitleLanguagePacks(dramaId).then((d) => !cancelled && setData(d), (e: unknown) => !cancelled && setError(e))
    return () => {
      cancelled = true
    }
  }, [dramaId])

  const change = (id: string, patch: { enabled?: boolean; style?: string }) => {
    if (!data) return
    setSaved(null)
    setTitleLanguagePacks(dramaId, { packs: choiceAfter(data.packs, { id, ...patch }) }).then(setData, setError)
  }
  const makeDefault = () => {
    if (!data) return
    const lang = data.source_language
    setLanguagePackDefault(lang, { packs: choiceAfter(data.packs, { id: '' }) }).then(
      () => setSaved(`Saved. ${PACK_LANGUAGE_NAMES[lang] ?? 'These'} titles without their own choice now use these packs.`),
      setError,
    )
  }

  const inGlossary = new Set(glossarySources)
  const packs = data?.packs ?? []
  const langName = PACK_LANGUAGE_NAMES[data?.source_language ?? ''] ?? 'this language'
  return (
    <Section title="Language packs" storageKey="glossary.languagePacks" summary={packsSummary(packs)}>
      <div className="lp">
        <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ serverText: true }} />
        <p className="muted">
          Starter packs, review them. Every entry is a draft. Packs are off until you turn them on for this title.
          Only the entries whose words appear in the text are sent, and a term in your own glossary always wins over
          a pack entry.
        </p>
        {data?.uses_default && packs.some((p) => p.enabled) && (
          <p className="muted">This title follows the default for {langName} titles. Changing a pack here gives it its own choice.</p>
        )}
        {data && packs.length === 0 && <p className="muted">There are no packs for {langName}.</p>}
        {packs.map((pack) => (
          <div key={pack.id} className="lp-pack">
            <div className="lp-head">
              <div>
                <strong>{pack.title}</strong>
                <div className="muted">Version {pack.version} · {pack.entry_count} entries</div>
              </div>
              <Toggle
                checked={pack.enabled}
                onChange={(on) => change(pack.id, { enabled: on })}
                aria-label={`Use ${pack.title} for this title`}
              />
            </div>
            <p className="muted">{pack.description}</p>
            {Object.keys(pack.styles.options).length > 1 && (
              <label className="lp-style">
                <span>How to write them</span>
                <select value={pack.style ?? pack.styles.default} onChange={(e) => change(pack.id, { style: e.target.value })}>
                  {Object.entries(pack.styles.options).map(([id, label]) => (
                    <option key={id} value={id}>{label}</option>
                  ))}
                </select>
              </label>
            )}
            <PackEntries pack={pack} inGlossary={inGlossary} canAdd={hasSeries} onAdded={onAdded} onError={setError} />
          </div>
        ))}
        {!hasSeries && packs.length > 0 && <p className="muted" id="lp-add-reason">Still needed to add entries to your glossary: a series (above).</p>}
        {packs.length > 0 && (
          <div className="lp-actions">
            <button type="button" className={buttonClass('secondary')} onClick={makeDefault}>
              Use this choice for {langName} titles
            </button>
            <span className="muted">{saved ?? 'Titles without their own choice follow it. Changing it needs the admin.'}</span>
          </div>
        )}
      </div>
    </Section>
  )
}
