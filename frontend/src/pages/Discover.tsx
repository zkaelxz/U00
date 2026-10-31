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
import { useEffect, useState } from 'react'

import { translateApi, engineOffReason, engineShortName } from '../api/translate'
import { PageShell } from '../components/PageShell'
import { Field } from '../components/Field'
import { RetryBanner } from '../components/RetryBanner'
import { Section } from '../components/Section'
import { Tabs } from '../components/Tabs'
import { tabButtonId, tabPanelId } from '../components/tabIds'
import { usePersistedState } from '../hooks/usePersistedState'
import { usePcOnly } from '../hooks/usePcOnly'
import { routeHref } from '../router'
import type { TranslateEngine } from '../types/translate'
import { AddTitle } from './discover/AddTitle'
import { BaihehubPanel } from './discover/BaihehubPanel'
import { BulkImport } from './discover/BulkImport'
import { CatalogPanel } from './discover/CatalogPanel'
import { OffEngineHelp } from './reader/ReaderEngine'
import { CATALOGUE_TAB_ENABLED, discoverEngines, discoverEnginesOff } from './discover/discoverFormat'
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
// The tab used to be stored raw under this key; read it once when the new preference is empty.
const LEGACY_TAB_KEY = 'baihe.discover.tab'

function legacyTab(): string {
  try {
    return window.localStorage.getItem(LEGACY_TAB_KEY) ?? ''
  } catch {
    return ''
  }
}

export default function DiscoverPage() {
  const pc = usePcOnly()
  const [engines, setEngines] = useState<TranslateEngine[] | null>(null)
  const [enginesOff, setEnginesOff] = useState<TranslateEngine[]>([])
  const [enginesError, setEnginesError] = useState<unknown>(null)
  const [enginesTick, setEnginesTick] = useState(0)
  const [engine, setEngine] = useState('')
  const [catalogKey, setCatalogKey] = useState(0)
  const [savedTab, setTab] = usePersistedState('discover.tab', '')
  const remembered = savedTab || legacyTab()
  const tab: TabId = TABS.find((t) => t.id === remembered)?.id ?? DEFAULT_TAB

  useEffect(() => {
    translateApi.engineList().then(
      ({ items, default_engine }) => {
        const usable = discoverEngines(items)
        setEngines(usable)
        setEnginesOff(discoverEnginesOff(items))
        setEngine((cur) => cur || (usable.find((e) => e.name === default_engine) ?? usable[0])?.name || '')
      },
      (e: unknown) => {
        setEngines([])
        setEnginesError(e)
      },
    )
  }, [enginesTick])

  const aiReady = !!engine
  const reloadCatalog = () => setCatalogKey((k) => k + 1)

  return (
    <PageShell
      width="wide"
      title="Discover"
      meta="Find baihe titles online and add the ones you want."
      className="discover-page"
      headExtra={(
        <>
        {engines === null ? (
          <p className="muted">Loading engines…</p>
        ) : enginesError ? (
          <RetryBanner error={enginesError} what="the AI engines" onRetry={() => {
            setEnginesError(null)
            setEngines(null)
            setEnginesTick((n) => n + 1)
          }} />
        ) : engines.length === 0 ? (
          <>
            <p className="muted" data-testid="no-engine">
              No AI engine is set up. Search links still work; <a href={routeHref({ name: 'settings' })}>add a key in
              Settings</a> for translation and page reading.
            </p>
            <OffEngineHelp off={enginesOff} />
          </>
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
                {enginesOff.map((e) => (
                  <option key={e.name} value={e.name} disabled>
                    {engineShortName(e)} ({engineOffReason(e)})
                  </option>
                ))}
              </select>
            </Field>
            <OffEngineHelp off={enginesOff} />
          </div>
        )}
        </>
      )}
      tabs={(
        <Tabs label="Discover tasks" idPrefix="discover" tabs={TABS} active={tab} onSelect={setTab} />
      )}
    >
      {CATALOGUE_TAB_ENABLED && (
        <div role="tabpanel" id={tabPanelId('discover', 'catalogue')} aria-labelledby={tabButtonId('discover', 'catalogue')} hidden={tab !== 'catalogue'} className="discover-panel">
          <CatalogPanel pc={pc} reloadKey={catalogKey} />
        </div>
      )}

      <div role="tabpanel" id={tabPanelId('discover', 'find')} aria-labelledby={tabButtonId('discover', 'find')} hidden={tab !== 'find'} className="discover-panel">
        <section aria-label="Find on official platforms" className="discover-group">
          <h2>Find on official platforms</h2>
          <FindPanel engine={engine} canTranslate={aiReady} />
        </section>
        <Section title="Search baihehub" summary="Chinese titles database" storageKey="discover.baihehub" defaultOpen>
          <BaihehubPanel engine={engine} canTranslate={aiReady} />
        </Section>
        <Section title="Open a site or explain a page" summary="New tab, or steps for a site you don't read" storageKey="discover.nav">
          <NavigationHelp engine={engine} aiReady={aiReady} />
        </Section>
      </div>

      <div role="tabpanel" id={tabPanelId('discover', 'add')} aria-labelledby={tabButtonId('discover', 'add')} hidden={tab !== 'add'} className="discover-panel">
        <section aria-label="Add a title" className="discover-group">
          <h2>Add a title</h2>
          <AddTitle engine={engine} aiReady={aiReady} onAdded={reloadCatalog} />
        </section>
        <Section title="Bulk import from listing pages" summary="Up to 10 tag or ranking pages" storageKey="discover.bulk">
          <BulkImport engine={engine} aiReady={aiReady} onAdded={reloadCatalog} />
        </Section>
      </div>
    </PageShell>
  )
}
