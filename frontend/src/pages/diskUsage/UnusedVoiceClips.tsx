/*
 * "Unused voice clips" under Library tools > Disk usage: the reference clips in
 * each title's voice_refs/ folder that no speaker points to. The server sends
 * only the file type, size, date and an opaque id (no file name or path) and
 * checks every clip again before moving it, so a clip a speaker started using
 * meanwhile is skipped. Moving goes to Baihe's Trash, where it can be restored.
 */
import { useState } from 'react'

import { trashUnusedVoiceClips } from '../../api/diskUsage'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import type { UnusedVoiceClip, UnusedVoiceClipList } from '../../types/diskUsage'
import {
  UNUSED_CLIPS_INTRO, clipLine, clipTitle, clipsInUseText, clipsText, describeClipsMoved, formatBytes,
} from './diskUsageModel'

const SERVER = { pcOnly: true, serverText: true } as const

type Props = {
  clips: UnusedVoiceClipList | null
  onChanged: (message: string) => void
  onStale: () => void
}

export function UnusedVoiceClips({ clips, onChanged, onStale }: Props) {
  const [busy, setBusy] = useState<string | 'all' | null>(null)
  const [error, setError] = useState<unknown>(null)
  if (!clips) return null
  const blocked = clips.busy_reason
  const move = (which: string | 'all', chosen: UnusedVoiceClip[]) => {
    setBusy(which)
    setError(null)
    trashUnusedVoiceClips(chosen).then(
      (r) => { setBusy(null); onChanged(describeClipsMoved(r)) },
      (e: unknown) => {
        setBusy(null)
        setError(e)
        // A 409 means a job started or the library is busy: show the new state.
        if ((e as { status?: number } | null)?.status === 409) onStale()
      },
    )
  }
  const all = clips.titles.flatMap((t) => t.clips)
  return (
    <section aria-label="Unused voice clips" className="du-trash du-clips">
      <h3 className="du-trash-title">Unused voice clips</h3>
      <p className="muted du-why">{UNUSED_CLIPS_INTRO}</p>
      {clips.total_count === 0 ? (
        <p className="muted">No unused voice clips.</p>
      ) : (
        <>
          <p className="du-trash-line" data-testid="clips-line">
            {`${clipsText(clips.total_count)} · ${formatBytes(clips.total_bytes)}`}
          </p>
          <div className="du-actions">
            <ConfirmButton
              name="all unused voice clips"
              label="Move all to Trash…"
              ariaLabel="Move all unused voice clips to Trash"
              confirmLabel={`Confirm: move ${clipsText(clips.total_count)} (${formatBytes(clips.total_bytes)}) to Trash`}
              verb="move to Trash"
              busy={busy === 'all'}
              disabled={!!blocked || (busy !== null && busy !== 'all')}
              onConfirm={() => move('all', all)}
            />
          </div>
          {clips.titles.map((t) => (
            <div key={`${t.title}:${t.clips[0]?.id}`} className="du-clip-title">
              <h4 className="du-clip-heading">{`${clipTitle(t.title)} · ${clipsText(t.clips.length)} · ${formatBytes(t.size_bytes)}`}</h4>
              <ul className="du-list" aria-label={`Unused clips in ${clipTitle(t.title)}`}>
                {t.clips.map((c, i) => (
                  <li key={c.id} className="du-row du-trash-row">
                    <div className="du-row-main">
                      <span className="du-name">{clipLine(c)}</span>
                    </div>
                    <div className="du-actions">
                      <ConfirmButton
                        name="clip"
                        label="Move to Trash…"
                        ariaLabel={`Move clip ${i + 1} of ${clipTitle(t.title)} to Trash`}
                        confirmLabel={`Confirm: move clip (${formatBytes(c.size_bytes)}) to Trash`}
                        verb="move to Trash"
                        busy={busy === c.id}
                        disabled={!!blocked || (busy !== null && busy !== c.id)}
                        onConfirm={() => move(c.id, [c])}
                      />
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </>
      )}
      {clips.titles_in_use > 0 && <p className="muted du-busy">{clipsInUseText(clips.titles_in_use)}</p>}
      {blocked && <p className="muted du-busy">{blocked}</p>}
      <ErrorBanner error={error} describe={SERVER} onDismiss={() => setError(null)} />
    </section>
  )
}
