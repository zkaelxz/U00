/*
 * Discover page (#/discover), ported from tabs/discover_tab.py
 * (inventory section 9, DI01-DI10).
 *
 * One engine picker at the top is used by every AI action on the page
 * (DI01); only its name is sent, the key stays on the PC. Sections:
 * Catalogue (search, filters, details, add to Library, remove: DI02/DI03),
 * Find on official platforms (DI04),
 * Add a title (from a URL or by hand: DI09/DI10), and under More tools
 * (folded Sections, rarely used): Search baihehub (DI06), Site navigation
 * helper (DI05), Bulk import (DI07), Open a site (DI08: a new tab, not an
 * in-app frame). Frequent blocks are Cards (docs/design/ui-refresh-spec.md
 * rule 16); one column at every width, capped at 860px.
 */
import { useEffect, useState } from 'react'

import { translateApi, engineShortName } from '../api/translate'
import { Card } from '../components/Card'
import { Field } from '../components/Field'
import { Section } from '../components/Section'
import { usePcOnly } from '../hooks/usePcOnly'
import { routeHref } from '../router'
import type { TranslateEngine } from '../types/translate'
import { AddTitle } from './discover/AddTitle'
import { BaihehubPanel } from './discover/BaihehubPanel'
import { BulkImport } from './discover/BulkImport'
import { CatalogPanel } from './discover/CatalogPanel'
import { discoverEngines, isHttpUrl } from './discover/discoverFormat'
import { ExternalLink } from './discover/ExternalLink'
import { FindPanel } from './discover/FindPanel'
import { NavigationHelp } from './discover/NavigationHelp'
import './discover/discover.css'

export default function DiscoverPage() {
  const pc = usePcOnly()
  const [engines, setEngines] = useState<TranslateEngine[] | null>(null)
  const [engine, setEngine] = useState('')
  const [catalogKey, setCatalogKey] = useState(0)

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
        <p className="muted">Find baihe titles, keep a catalogue of them, and add the ones you want to your Library.</p>
        {engines === null ? (
          <p className="muted">Loading engines…</p>
        ) : engines.length === 0 ? (
          <p className="muted" data-testid="no-engine">
            No AI engine is set up. The catalogue and search links work; <a href={routeHref({ name: 'settings' })}>add a key in
            Settings</a> for translation and page reading.
          </p>
        ) : (
          <div className="discover-engine">
            <Field label="AI engine" help="Used for every AI action on this page: translating a search, reading a listing or page.">
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

      <Card title="Catalogue" aria-label="Catalogue">
        <CatalogPanel pc={pc} reloadKey={catalogKey} />
      </Card>
      <Card title="Find on official platforms" aria-label="Find on official platforms">
        <FindPanel engine={engine} canTranslate={aiReady} />
      </Card>
      <Card title="Add a title" aria-label="Add a title">
        <AddTitle engine={engine} aiReady={aiReady} onAdded={reloadCatalog} />
      </Card>
      <Card title="More tools" aria-label="More tools">
        <Section title="Search baihehub" summary="Chinese titles database" storageKey="discover.baihehub">
          <BaihehubPanel engine={engine} canTranslate={aiReady} />
        </Section>
        <Section title="Site navigation helper" summary="Steps for a site you don't read" storageKey="discover.nav">
          <NavigationHelp engine={engine} aiReady={aiReady} />
        </Section>
        <Section title="Bulk import from listing pages" summary="Up to 10 tag or ranking pages" storageKey="discover.bulk">
          <BulkImport engine={engine} aiReady={aiReady} onAdded={reloadCatalog} />
        </Section>
        <Section title="Open a site" summary="In a new tab" storageKey="discover.open">
          <OpenSite />
        </Section>
      </Card>
    </div>
  )
}

// DI08. The Streamlit tab embedded the site in a frame; most large sites
// forbid that, so this opens a normal tab to use next to the helper above.
function OpenSite() {
  const [url, setUrl] = useState('')
  return (
    <div className="discover-block">
      <p className="muted discover-lead">
        Opens a site in a new browser tab, to use side by side with the navigation helper. (Most sites refuse to be shown
        inside another app.)
      </p>
      <div className="discover-row">
        <Field label="Site URL">
          <input type="url" value={url} maxLength={2000} onChange={(e) => setUrl(e.target.value)} placeholder="https://" />
        </Field>
        {isHttpUrl(url) ? (
          <span className="discover-open">
            <ExternalLink href={url}>Open in a new tab</ExternalLink>
          </span>
        ) : (
          <span className="muted discover-open">Enter an http(s) address.</span>
        )}
      </div>
    </div>
  )
}
