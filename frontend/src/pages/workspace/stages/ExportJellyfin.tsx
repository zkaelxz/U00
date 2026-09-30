/*
 * Export > Send to Jellyfin (roadmap Step 39). Shown only while the Jellyfin
 * connector is on (Settings), and only at the PC. Puts this drama's
 * subtitles next to a matching Jellyfin item's video, or into a new title
 * folder in the library (optionally with the video), then asks Jellyfin to
 * rescan. An existing file is only replaced when "Replace existing files"
 * is on.
 */
import { useEffect, useState } from 'react'

import { getJellyfinConfig, scanJellyfin, sendToJellyfin } from '../../../api/jellyfin'
import { getPcMode, loadPcMode } from '../../../api/pcOnly'
import { Field } from '../../../components/Field'
import { Section } from '../../../components/Section'
import { Toggle } from '../../../components/Toggle'
import { buttonClass } from '../../../components/uiClasses'
import type { JellyfinConfig, JellyfinLanguage, JellyfinScanItem, JellyfinSendRequest } from '../../../types/jellyfin'
import { itemLabel, jellyfinErrorMessage, readyToSend, sendResultText } from '../../settings/jellyfin'
import { useStage } from '../StageContext'

type Target = 'item' | 'folder'

export function ExportJellyfin({ field }: { field: 'en' | 'zh' | 'bilingual' }) {
  const { dramaId, drama } = useStage()
  // The language the file is written in: the drama's own for the original text.
  const src = drama.source_language
  const language: JellyfinLanguage =
    field === 'zh' ? (src === 'ja' || src === 'ko' || src === 'zh' ? src : 'zh') : 'en'
  const [cfg, setCfg] = useState<JellyfinConfig | null>(null)
  const [target, setTarget] = useState<Target>('folder')
  const [items, setItems] = useState<JellyfinScanItem[] | null>(null)
  const [itemId, setItemId] = useState('')
  const [format, setFormat] = useState<'srt' | 'ass'>('srt')
  const [media, setMedia] = useState<JellyfinSendRequest['media']>('none')
  const [overwrite, setOverwrite] = useState(false)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    void loadPcMode().then(() => {
      if (!live || getPcMode() === 'remote') return
      getJellyfinConfig().then((c) => live && setCfg(c), () => undefined)
    })
    return () => {
      live = false
    }
  }, [])

  if (!cfg?.enabled) return null // the connector is off (or not at the PC)

  const loadItems = () => {
    setBusy(true)
    setError(null)
    scanJellyfin(language).then(
      (r) => {
        const writable = r.items.filter((i) => i.writable)
        setItems(writable)
        setItemId(writable[0]?.id ?? '')
        setBusy(false)
      },
      (e: unknown) => {
        setError(jellyfinErrorMessage(e))
        setBusy(false)
      },
    )
  }

  const send = () => {
    setBusy(true)
    setNote(null)
    setError(null)
    const body: JellyfinSendRequest = {
      format, field, language, overwrite, refresh: true,
      media: target === 'folder' ? media : 'none',
      ...(target === 'item' ? { item_id: itemId } : {}),
    }
    sendToJellyfin(dramaId, body).then(
      (r) => {
        setNote(sendResultText(r))
        setBusy(false)
      },
      (e: unknown) => {
        setError(jellyfinErrorMessage(e))
        setBusy(false)
      },
    )
  }

  const canSend = readyToSend(cfg) && !busy && (target === 'folder' || !!itemId)

  return (
    <section className="panel" aria-label="Send to Jellyfin">
      <Section storageKey="export.jellyfin" title="Send to Jellyfin" summary="put the subtitles in your Jellyfin library">
        <div className="source-panel">
          {!readyToSend(cfg) && (
            <p className="muted">Finish the Jellyfin setup in Settings (address, API key and library folder).</p>
          )}
          <Field label="Where" help="Next to a video Jellyfin already has, or as a new title folder in the library.">
            <select value={target} onChange={(e) => setTarget(e.target.value as Target)}>
              <option value="folder">New title folder</option>
              <option value="item">Next to a Jellyfin video</option>
            </select>
          </Field>
          {target === 'item' && (
            items === null ? (
              <div>
                <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy} onClick={loadItems}>
                  Find videos missing subtitles
                </button>
              </div>
            ) : items.length === 0 ? (
              <p className="muted">No video in the library folder is missing these subtitles.</p>
            ) : (
              <Field label="Jellyfin video">
                <select value={itemId} onChange={(e) => setItemId(e.target.value)}>
                  {items.map((i) => <option key={i.id} value={i.id}>{itemLabel(i)}</option>)}
                </select>
              </Field>
            )
          )}
          <Field label="Format">
            <select value={format} onChange={(e) => setFormat(e.target.value as 'srt' | 'ass')}>
              <option value="srt">SRT</option>
              <option value="ass">ASS (styled)</option>
            </select>
          </Field>
          {target === 'folder' && (
            <Field label="Video" help="Jellyfin needs a video in the folder to show the subtitles. On the same drive the copy is instant.">
              <select value={media} onChange={(e) => setMedia(e.target.value as JellyfinSendRequest['media'])}>
                <option value="none">Subtitles only</option>
                <option value="source">Copy the source video</option>
                <option value="dubbed">Copy the dubbed video</option>
              </select>
            </Field>
          )}
          <Field label="Replace existing files" help="Off: a file already in the library is never overwritten.">
            <Toggle checked={overwrite} onChange={setOverwrite} />
          </Field>
          <div>
            <button type="button" className={buttonClass('primary')} disabled={!canSend} onClick={send}>
              {busy ? 'Working…' : 'Send to Jellyfin'}
            </button>
          </div>
          {note && <p role="status">{note}</p>}
          {error && <p className="error" role="alert">{error}</p>}
        </div>
      </Section>
    </section>
  )
}
