/* Followed channels that are live right now, first on the Live page: one tap starts captions with the channel's saved options. */
import { useState } from 'react'

import { announceLiveStarted, describeFollowError, followName, followOriginalName, startFromFollow } from '../../api/liveFollows'
import { Badge } from '../../components/Badge'
import { buttonClass } from '../../components/uiClasses'
import type { LiveFollow } from '../../types/live'

export function LiveNowCards({ items, picked }: { items: LiveFollow[]; picked: number | null }) {
  const [busy, setBusy] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const live = items.filter((f) => f.live_now)
  // A channel picked from the bell's link goes first so the Watch button is where the eye lands.
  live.sort((a, b) => Number(b.id === picked) - Number(a.id === picked))
  if (!live.length) return null

  const watch = async (f: LiveFollow) => {
    setBusy(f.id)
    setError(null)
    try {
      const r = await startFromFollow(f.id)
      announceLiveStarted(r.session_id)
    } catch (err) {
      setError(describeFollowError(err))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="live-now" data-testid="live-now">
      <ul className="live-now-list" aria-label="Live now">
        {live.map((f) => {
          const name = followName(f)
          const original = followOriginalName(f)
          return (
            <li key={f.id} className="live-now-card" data-testid="live-now-card" data-picked={f.id === picked || undefined}>
              <div className="live-follow-head">
                <span className="live-follow-name">{name}</span>
                {original && <small className="muted live-follow-original">{original}</small>}
                <Badge tone="accent">Live</Badge>
              </div>
              <button type="button" className={buttonClass('primary')} disabled={busy !== null}
                title="Start captions with this channel’s saved settings."
                onClick={() => void watch(f)}>
                {busy === f.id ? 'Starting…' : 'Watch with subtitles'}
              </button>
            </li>
          )
        })}
      </ul>
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  )
}
