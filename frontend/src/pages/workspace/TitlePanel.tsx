/*
 * The side panel of a Simple audio or video title page: Video, Translate, Glossary, Episodes and Export in one tabbed box beside the lines.
 */
import { useEffect, useState, type ReactNode } from 'react'

import { engineOptionLabel } from '../../api/translate'
import { getTranslateConfig } from '../../api/translateStage'
import { mediaStreamUrl } from '../../api/media'
import { getMediaStatus } from '../../api/workspace'
import { ButtonLink } from '../../components/Button'
import { humanize } from '../../components/labels'
import { Tabs, type TabInfo } from '../../components/Tabs'
import { tabButtonId, tabPanelId } from '../../components/tabIds'
import { routeHref } from '../../router'
import { initialForm, loadPresetStart } from './translateForm'
import './titlePanel.css'

type PanelTab = 'video' | 'translate' | 'glossary' | 'episodes' | 'export'
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

// The same engine, model, style and variant the Translate page opens with; null when the settings cannot be read.
function useTranslateSummary(id: number): string | null {
  const [text, setText] = useState<string | null>(null)
  useEffect(() => {
    let cancelled = false
    getTranslateConfig(id).then(
      (c) => {
        if (cancelled) return
        const f = initialForm(c, loadPresetStart(id))
        const engine = c.engines.find((e) => e.name === c.translation_engine)
        const style = c.style_presets.find((p) => p.key === f.style_preset)?.label ?? ''
        setText([engine ? engineOptionLabel(engine) : c.translation_engine, f.model, style, humanize('locale', f.locale)].filter(Boolean).join(' · '))
      },
      () => !cancelled && setText(null),
    )
    return () => {
      cancelled = true
    }
  }, [id])
  return text
}

export function TitlePanel({ id, episodes }: { id: number; episodes: ReactNode }) {
  const hasVideo = useSourceVideo(id)
  const summary = useTranslateSummary(id)
  const tabs: TabInfo<PanelTab>[] = [
    ...(hasVideo ? [{ id: 'video' as const, label: 'Video' }] : []),
    { id: 'translate', label: 'Translate' },
    { id: 'glossary', label: 'Glossary' },
    { id: 'episodes', label: 'Episodes' },
    { id: 'export', label: 'Export' },
  ]
  const [picked, setPicked] = useState<PanelTab | null>(null)
  // The first open tab is Video when there is one, else Translate; a pick that has since disappeared falls back the same way.
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
      {panel('translate', (
        <>
          <p className="title-panel-note" data-testid="panel-translate-summary">{summary ?? 'Engine, style and English variant are on the Translate page.'}</p>
          <div className="actions">
            <ButtonLink href={stage('translate')}>Change</ButtonLink>
            <ButtonLink href={routeHref({ name: 'drama', id, stage: 'translate', focus: 'estimate' })}>Estimate cost</ButtonLink>
          </div>
        </>
      ))}
      {panel('glossary', (
        <>
          <p className="title-panel-note">Names and terms the translation keeps the same.</p>
          <div className="actions"><ButtonLink href={stage('translate', 'glossary')}>Open glossary</ButtonLink></div>
        </>
      ))}
      {panel('episodes', episodes)}
      {panel('export', (
        <>
          <p className="title-panel-note">Subtitle file (SRT, VTT, ASS) or a video with a subtitle track.</p>
          <div className="actions"><ButtonLink href={stage('export')}>Export</ButtonLink></div>
        </>
      ))}
    </aside>
  )
}
