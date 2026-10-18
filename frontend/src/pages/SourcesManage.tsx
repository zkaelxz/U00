/*
 * The Manage sources tab of Sources (PC only): the site list, timing and cache, the network
 * settings and the pasted-link log as one list of folds, the first open. Other devices see one line.
 * The old link "#/sources/manage?tab=network" lands here and scrolls to that area.
 */
import { useCallback, useEffect, useState } from 'react'

import { getPcMode, loadPcMode } from '../api/pcOnly'
import { clearSourcesCache, getSourcesSettings, listSources } from '../api/sources'
import { Badge } from '../components/Badge'
import { ErrorBanner } from '../components/ErrorBanner'
import { useMediaQuery } from '../hooks/useMediaQuery'
import type { SourceHealth, SourcesSettings, SourceSummary } from '../types/sources'
import { useDeveloperMode } from './assistant/developerMode'
import { ActivityTab } from './sources/ActivityTab'
import { emptyTabMessage, manageAreaId, type ManageTab } from './sources/manageTabs'
import { NetworkTab } from './sources/NetworkTab'
import { SitesTab } from './sources/SitesTab'
import { pageSummary } from './sources/sourcesFormat'
import { settingsSummary } from './sources/sourcesSettings'
import { TimingTab } from './sources/TimingTab'
import './sources/sources.css'

export default function SourcesManagePanel({ area }: { area?: ManageTab }) {
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

  // The areas mount late (they wait for their data), so scroll once the one the link names exists.
  const ready = sources !== null && settingsDone
  useEffect(() => {
    if (area && ready) document.getElementById(manageAreaId(area))?.scrollIntoView({ block: 'start' })
  }, [area, ready])

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

  const loading = <p className="muted">Loading…</p>
  const unavailable = settingsDone ? <p className="muted">{emptyTabMessage('timing')}</p> : loading
  const summary = sources ? pageSummary(sources) : null

  return (
    <div className="manage-panel">
      {sources && summary && (
        <div className="manage-summaries">
          <p className="page-meta" data-testid="sources-summary">
            {summary.on} · {summary.paused ? <Badge tone="warn">{summary.paused} paused</Badge> : '0 paused'}
          </p>
          <p className="page-meta" data-testid="manage-summary">{settingsSummary(settings, sources)}</p>
        </div>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ pcOnly: true, serverText: true }} />
      <div id={manageAreaId('sites')} className="manage-area">
        {!sources ? loading : sources.length === 0
          ? <p className="muted">{emptyTabMessage('sites')}</p>
          : <SitesTab phone={phone} sources={sources} settings={settings} onSource={replaceSource} onHealth={setHealth} />}
      </div>
      <div id={manageAreaId('timing')} className="manage-area">
        {settings
          ? <TimingTab settings={settings} developerMode={developerMode} onSaved={setSettings} onClearCache={clearCache} clearing={clearing} />
          : unavailable}
      </div>
      <div id={manageAreaId('network')} className="manage-area">
        {settings ? <NetworkTab settings={settings} onSaved={setSettings} /> : unavailable}
      </div>
      <div id={manageAreaId('activity')} className="manage-area">
        <ActivityTab />
      </div>
    </div>
  )
}
