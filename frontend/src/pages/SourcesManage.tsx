/*
 * Manage sources (#/sources/manage, PC only): the site list, timing and cache, the network
 * settings and the pasted-link log, split over four tabs so each is short. The tab is in the URL
 * ("?tab=network"), so Back returns to the tab the person came from. Other devices see one line.
 */
import { useCallback, useEffect, useState, type ReactNode } from 'react'

import { getPcMode, loadPcMode } from '../api/pcOnly'
import { clearSourcesCache, getSourcesSettings, listSources } from '../api/sources'
import { ButtonLink } from '../components/Button'
import { ErrorBanner } from '../components/ErrorBanner'
import { Tabs } from '../components/Tabs'
import { tabButtonId, tabPanelId } from '../components/tabIds'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { PC_ONLY_BODY, PC_ONLY_SUMMARY, usePcOnly } from '../hooks/usePcOnly'
import { Breadcrumbs } from '../nav/BreadcrumbNav'
import { routeCrumbs } from '../nav/breadcrumbs'
import type { SourceHealth, SourcesSettings, SourceSummary } from '../types/sources'
import { useDeveloperMode } from './assistant/developerMode'
import { ActivityTab } from './sources/ActivityTab'
import { MANAGE_TABS, emptyTabMessage, manageHref, type ManageTab } from './sources/manageTabs'
import { NetworkTab } from './sources/NetworkTab'
import { SitesTab } from './sources/SitesTab'
import { settingsSummary } from './sources/sourcesSettings'
import { TimingTab } from './sources/TimingTab'
import './sources/sources.css'

const PREFIX = 'sources-manage'

export default function SourcesManagePage({ tab = 'sites' }: { tab?: ManageTab }) {
  const pc = usePcOnly()
  return (
    <main className="library-page sources-manage-page">
      <Breadcrumbs crumbs={routeCrumbs({ name: 'sources-manage' })} />
      <header className="page-head">
        <div className="page-head-text">
          <h2 className="page-title">Manage sources</h2>
        </div>
        <ButtonLink variant="secondary" href="#/sources">Back to Sources</ButtonLink>
      </header>
      {pc === 'remote' ? <p className="muted">{`${PC_ONLY_SUMMARY}. ${PC_ONLY_BODY}`}</p> : <ManageBody tab={tab} />}
    </main>
  )
}

function ManageBody({ tab }: { tab: ManageTab }) {
  const phone = useMediaQuery('(max-width: 640px)')
  const developerMode = useDeveloperMode()
  const [sources, setSources] = useState<SourceSummary[] | null>(null)
  const [settings, setSettings] = useState<SourcesSettings | null>(null)
  const [settingsDone, setSettingsDone] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [clearing, setClearing] = useState(false)

  // Wait for /api/meta so a remote viewer never sends a settings request.
  useEffect(() => {
    let off = false
    void loadPcMode().then(() => {
      if (off || getPcMode() === 'remote') return
      listSources().then((s) => !off && setSources(s), (e: unknown) => !off && setError(e))
      getSourcesSettings()
        .then((s) => !off && setSettings(s), (e: unknown) => !off && setError(e))
        .finally(() => !off && setSettingsDone(true))
    })
    return () => {
      off = true
    }
  }, [])

  const replaceSource = useCallback(
    (s: SourceSummary) => setSources((cur) => (cur ? cur.map((x) => (x.name === s.name ? s : x)) : cur)),
    [],
  )
  const setHealth = useCallback(
    (name: string, h: SourceHealth) =>
      setSources((cur) => (cur ? cur.map((x) => (x.name === name ? { ...x, health: h.light } : x)) : cur)),
    [],
  )

  async function clearCache() {
    setError(null)
    setClearing(true)
    try {
      const cache = await clearSourcesCache()
      setSettings((cur) => (cur ? { ...cur, cache } : cur))
    } catch (e) {
      setError(e)
    } finally {
      setClearing(false)
    }
  }

  const select = (id: ManageTab) => {
    window.location.hash = manageHref(id)
  }
  const panel = (id: ManageTab, children: ReactNode) => (
    <div key={id} id={tabPanelId(PREFIX, id)} role="tabpanel" aria-labelledby={tabButtonId(PREFIX, id)} className="manage-panel" hidden={tab !== id}>
      {children}
    </div>
  )
  const loading = <p className="muted">Loading…</p>
  const unavailable = settingsDone ? <p className="muted">{emptyTabMessage('timing')}</p> : loading

  return (
    <>
      {sources && <p className="page-meta" data-testid="manage-summary">{settingsSummary(settings, sources)}</p>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ pcOnly: true, serverText: true }} />
      <Tabs label="Manage sources" idPrefix={PREFIX} tabs={MANAGE_TABS} active={tab} onSelect={select} />
      {panel('sites', !sources ? loading : sources.length === 0
        ? <p className="muted">{emptyTabMessage('sites')}</p>
        : <SitesTab phone={phone} sources={sources} settings={settings} onSource={replaceSource} onHealth={setHealth} />)}
      {panel('timing', settings
        ? <TimingTab settings={settings} developerMode={developerMode} onSaved={setSettings} onClearCache={clearCache} clearing={clearing} />
        : unavailable)}
      {panel('network', settings ? <NetworkTab settings={settings} onSaved={setSettings} /> : unavailable)}
      {panel('activity', <ActivityTab />)}
    </>
  )
}
