/*
 * "Old source videos" under Library tools > Storage: videos of finished titles that are older than the chosen
 * age. Only titles with translated lines, not pinned by Keep original and with no job running are offered; the
 * subtitles and extracted audio stay. Moving goes to Baihe's Trash (restorable) and only after a confirm press.
 */
import { useEffect, useState } from 'react'

import { listRawVideos, trashRawVideos } from '../../api/diskUsage'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import type { RawVideoList } from '../../types/diskUsage'
import { formatDecimalBytes } from './diskUsageModel'

const AGES = [30, 90, 180]

export function RawVideosCard({ onChanged }: { onChanged: (message: string) => void }) {
  const [age, setAge] = useState(30)
  const [list, setList] = useState<RawVideoList | null>(null)
  const [busy, setBusy] = useState<string | 'all' | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [tick, setTick] = useState(0)
  useEffect(() => {
    listRawVideos(age).then(setList, setError)
  }, [age, tick])

  const move = async (which: string | 'all', chosen: RawVideoList['videos']) => {
    setBusy(which)
    setError(null)
    try {
      const done = await trashRawVideos(chosen, age)
      const skipped = done.skipped.length ? ` ${done.skipped.length} skipped because they changed.` : ''
      onChanged(`Moved ${done.moved_count} video${done.moved_count === 1 ? '' : 's'} (${formatDecimalBytes(done.moved_bytes)}) to Trash. Empty Trash to free the space.${skipped}`)
    } catch (e) {
      setError(e)
    } finally {
      setBusy(null)
      setTick((t) => t + 1)
    }
  }

  return (
    <section aria-label="Old source videos" className="du-trash" data-testid="raw-videos">
      <p className="muted du-why">
        Videos of finished titles you no longer need. The subtitles and the extracted audio stay, and a title you
        marked Keep original is never listed. Videos go to Trash first, so you can restore them.
      </p>
      <label>
        Older than{' '}
        <select value={age} onChange={(e) => setAge(Number(e.target.value))}>
          {AGES.map((d) => <option key={d} value={d}>{d} days</option>)}
        </select>
      </label>
      {list && list.videos.length === 0 && <p className="muted">No old source videos to clean.</p>}
      {list && list.videos.length > 0 && (
        <>
          <p className="du-trash-line">{`${list.videos.length} videos · ${formatDecimalBytes(list.total_bytes)}`}</p>
          <div className="du-actions">
            <ConfirmButton
              name="all old source videos"
              label="Move all to Trash…"
              ariaLabel="Move all old source videos to Trash"
              confirmLabel={`Confirm: move ${list.videos.length} videos (${formatDecimalBytes(list.total_bytes)}) to Trash`}
              verb="move to Trash"
              busy={busy === 'all'}
              disabled={!!list.busy_reason || (busy !== null && busy !== 'all')}
              onConfirm={() => move('all', list.videos)}
            />
          </div>
          <ul className="du-list" aria-label="Old source videos">
            {list.videos.map((v) => (
              <li key={v.id} className="du-row du-trash-row">
                <div className="du-row-main"><span className="du-name">{`${v.title || 'Untitled'} · ${formatDecimalBytes(v.size_bytes)}`}</span></div>
                <div className="du-actions">
                  <ConfirmButton
                    name="video"
                    label="Move to Trash…"
                    ariaLabel={`Move the video of ${v.title || 'this title'} to Trash`}
                    confirmLabel={`Confirm: move video (${formatDecimalBytes(v.size_bytes)}) to Trash`}
                    verb="move to Trash"
                    busy={busy === v.id}
                    disabled={!!list.busy_reason || (busy !== null && busy !== v.id)}
                    onConfirm={() => move(v.id, [v])}
                  />
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
      {list?.busy_reason && <p className="muted du-busy">{list.busy_reason}</p>}
      <ErrorBanner error={error} describe={{ pcOnly: true, serverText: true }} onDismiss={() => setError(null)} />
    </section>
  )
}
