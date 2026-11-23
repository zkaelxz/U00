/*
 * The panel of a Simple audio or video title page while it has no lines: Video, Glossary, Details and (in a series) Episodes in one tabbed box.
 * Translate and Export are left out until there are lines, since there is nothing to translate or export yet.
 */
import { useEffect, useState, type ReactNode } from 'react'

import { mediaStreamUrl } from '../../api/media'
import { getMediaStatus } from '../../api/workspace'
import { ButtonLink } from '../../components/Button'
import { Tabs, type TabInfo } from '../../components/Tabs'
import { tabButtonId, tabPanelId } from '../../components/tabIds'
import { routeHref } from '../../router'
import { SettingSources } from './SettingSources'
import './titlePanel.css'

type PanelTab = 'video' | 'glossary' | 'details' | 'episodes'
const PREFIX = 'title-panel'

// Null until the status answers; a title without a source video simply has no Video tab.
function useSourceVideo(id: number): boolean | null {
  const [has, setHas] = useState<boolean | null>(null)
  useEffect(() => {
    let cancelled = false
    getMediaStatus(id).then((s) => !cancelled && setHas(!!s.has_source_video), () => !cancelled && setHas(false))
    return () => {
      cancelled = true
    }
  }, [id])
  return has
}

export function TitlePanel({ id, episodes }: { id: number; episodes: ReactNode | null }) {
  const hasVideo = useSourceVideo(id)
  const tabs: TabInfo<PanelTab>[] = [
    ...(hasVideo ? [{ id: 'video' as const, label: 'Video' }] : []),
    { id: 'glossary', label: 'Glossary' },
    { id: 'details', label: 'Details' },
    // A title in no series has no other episodes to list.
    ...(episodes ? [{ id: 'episodes' as const, label: 'Episodes' }] : []),
  ]
  const [picked, setPicked] = useState<PanelTab | null>(null)
  // The first open tab is Video when there is one, else Glossary; a pick that has since disappeared falls back the same way.
  const active = tabs.find((t) => t.id === picked)?.id ?? tabs[0].id
  const stage = (s: string, tab?: 'glossary') => routeHref({ name: 'drama', id, stage: s, ...(tab ? { tab } : {}) })
  const panel = (tab: PanelTab, children: ReactNode) => (
    <div id={tabPanelId(PREFIX, tab)} role="tabpanel" aria-labelledby={tabButtonId(PREFIX, tab)} hidden={active !== tab} className="title-panel-body">
      {children}
    </div>
  )

  return (
    <aside className="panel title-panel" aria-label="Title panel">
      <Tabs label="Title panel sections" idPrefix={PREFIX} tabs={tabs} active={active} onSelect={setPicked} />
      {hasVideo && panel('video', active === 'video' && <video className="title-panel-video" controls preload="metadata" src={mediaStreamUrl(id, 'video')} />)}
      {panel('glossary', (
        <>
          <p className="title-panel-note">Names and terms the translation keeps the same.</p>
          <div className="actions"><ButtonLink href={stage('translate', 'glossary')}>Open glossary</ButtonLink></div>
        </>
      ))}
      {panel('details', (
        <>
          <p className="title-panel-note">The title's name, language, series, summary and source link.</p>
          <div className="actions"><ButtonLink href={routeHref({ name: 'drama', id, stage: 'source', tab: 'details' })}>Open details</ButtonLink></div>
          {active === 'details' && <SettingSources id={id} />}
        </>
      ))}
      {episodes && panel('episodes', episodes)}
    </aside>
  )
}
