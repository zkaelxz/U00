/*
 * Discover page (#/discover).
 *
 * One engine picker at the top is used by every AI action on the page
 * (DI01); only its name is sent, the key stays on the PC. Three tabs, the
 * choice remembered; every panel stays mounted (just hidden) so a half-filled
 * form or a bulk review survives a tab switch:
 * Catalogue (search, filters, details, add to Library, remove: DI02/DI03),
 * shown only while CATALOGUE_TAB_ENABLED is true;
 * Find a title: official platforms (DI04), then folded Search baihehub (DI06)
 * and Open a site or explain a page (DI05/DI08: a new tab, not an in-app frame);
 * Add titles: from a URL or by hand (DI09/DI10), then folded Bulk import (DI07).
 * One column at every width, capped at 860px.
 */
import { useEffect, useState, type KeyboardEvent } from 'react'

import { translateApi, engineShortName } from '../api/translate'
import { Field } from '../components/Field'
import { Section } from '../components/Section'
import { usePcOnly } from '../hooks/usePcOnly'
import { routeHref } from '../router'
import type { TranslateEngine } from '../types/translate'
import { AddTitle } from './discover/AddTitle'
import { BaihehubPanel } from './discover/BaihehubPanel'
import { BulkImport } from './discover/BulkImport'
import { CatalogPanel } from './discover/CatalogPanel'
import { CATALOGUE_TAB_ENABLED, discoverEngines } from './discover/discoverFormat'
import { FindPanel } from './discover/FindPanel'
import { NavigationHelp } from './discover/NavigationHelp'
import './discover/discover.css'
import { AI_ENGINE_LABEL } from '../helpText'

type TabId = 'catalogue' | 'find' | 'add'
const TABS: { id: TabId; label: string }[] = [
  ...(CATALOGUE_TAB_ENABLED ? [{ id: 'catalogue' as const, label: 'Catalogue' }] : []),
  { id: 'find', label: 'Find a title' },
  { id: 'add', label: 'Add titles' },
]
const DEFAULT_TAB: TabId = TABS[0].id
const TAB_KEY = 'baihe.discover.tab'

function savedTab(): TabId {
  try {
    const v = window.localStorage.getItem(TAB_KEY)
    return TABS.some((t) => t.id === v) ? (v as TabId) : DEFAULT_TAB
  } catch {
    return DEFAULT_TAB
  }
}

export default function DiscoverPage() {
  const pc = usePcOnly()
  const [engines, setEngines] = useState<TranslateEngine[] | null>(null)
  const [engine, setEngine] = useState('')
  const [catalogKey, setCatalogKey] = useState(0)
  const [tab, setTab] = useState<TabId>(savedTab)

  const pickTab = (id: TabId) => {
    setTab(id)
    try {
      window.localStorage.setItem(TAB_KEY, id)
    } catch {
      /* private window: the choice just isn't remembered */
    }
  }
  const onTabKey = (e: KeyboardEvent, id: TabId) => {
    const i = TABS.findIndex((t) => t.id === id)
    const next = e.key === 'ArrowRight' ? i + 1 : e.key === 'ArrowLeft' ? i - 1 : null
    if (next === null) return
    e.preventDefault()
    const to = TABS[(next + TABS.length) % TABS.length].id
    pickTab(to)
    document.getElementById(`discover-tab-${to}`)?.focus()
  }

  useEffect(() => {
    translateApi.engines().then(
      (all) => {
        const usable = discoverEngines(all)
        setEngines(usable)
        setEngine((cur) => cur || (usable.find((e) => e.name === 'claude') ?? usable[0])?.name || '')
      },
      () => setEngines([]),
    )
  }, [])

  const aiReady = !!engine
  const reloadCatalog = () => setCatalogKey((k) => k + 1)

  return (
    <div className="discover-page">
      <header className="discover-head">
        <h2>Discover</h2>
        <p className="muted">Find baihe titles online and add the ones you want.</p>
        {engines === null ? (
          <p className="muted">Loading engines…</p>
        ) : engines.length === 0 ? (
          <p className="muted" data-testid="no-engine">
            No AI engine is set up. Search links still work; <a href={routeHref({ name: 'settings' })}>add a key in
            Settings</a> for translation and page reading.
          </p>
        ) : (
          <div className="discover-engine">
            <Field label={AI_ENGINE_LABEL} help="Used for every AI action on this page: translating a search, reading a listing or page.">
              <select value={engine} onChange={(e) => setEngine(e.target.value)}>
                {engines.map((e) => (
                  <option key={e.name} value={e.name}>
                    {engineShortName(e)}
                    {e.free ? ' (free)' : ''}
                  </option>
                ))}
              </select>
            </Field>
          </div>
        )}
      </header>

      <div className="discover-tabs" role="tablist" aria-label="Discover tasks">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`discover-tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls={`discover-panel-${t.id}`}
            tabIndex={tab === t.id ? 0 : -1}
            className="discover-tab"
            onClick={() => pickTab(t.id)}
            onKeyDown={(e) => onTabKey(e, t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {CATALOGUE_TAB_ENABLED && (
        <div role="tabpanel" id="discover-panel-catalogue" aria-labelledby="discover-tab-catalogue" hidden={tab !== 'catalogue'} className="discover-panel">
          <CatalogPanel pc={pc} reloadKey={catalogKey} />
        </div>
      )}

      <div role="tabpanel" id="discover-panel-find" aria-labelledby="discover-tab-find" hidden={tab !== 'find'} className="discover-panel">
        <section aria-label="Find on official platforms" className="discover-group">
          <h3>Find on official platforms</h3>
          <FindPanel engine={engine} canTranslate={aiReady} />
        </section>
        <Section title="Search baihehub" summary="Chinese titles database" storageKey="discover.baihehub" defaultOpen>
          <BaihehubPanel engine={engine} canTranslate={aiReady} />
        </Section>
        <Section title="Open a site or explain a page" summary="New tab, or steps for a site you don't read" storageKey="discover.nav">
          <NavigationHelp engine={engine} aiReady={aiReady} />
        </Section>
      </div>

      <div role="tabpanel" id="discover-panel-add" aria-labelledby="discover-tab-add" hidden={tab !== 'add'} className="discover-panel">
        <section aria-label="Add a title" className="discover-group">
          <h3>Add a title</h3>
          <AddTitle engine={engine} aiReady={aiReady} onAdded={reloadCatalog} />
        </section>
        <Section title="Bulk import from listing pages" summary="Up to 10 tag or ranking pages" storageKey="discover.bulk">
          <BulkImport engine={engine} aiReady={aiReady} onAdded={reloadCatalog} />
        </Section>
      </div>
    </div>
  )
}
