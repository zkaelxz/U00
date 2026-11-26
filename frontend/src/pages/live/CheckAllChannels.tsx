/* The Check all button, first on the Live page so it never hides inside the collapsed Followed channels section. */
import { useState } from 'react'

import { checkAllFollows, describeFollowError } from '../../api/liveFollows'
import { buttonClass } from '../../components/uiClasses'
import type { Follows } from './useFollows'

export function CheckAllChannels({ follows }: { follows: Follows }) {
  const { list, load, alive, anyChecking } = follows
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  if (!list) return null

  const own = list.can_auto_start
  const changeable = list.items.filter((f) => f.mine || own)
  const reason = changeable.length === 0 ? 'Follow a channel first.' : anyChecking ? 'Checking… wait for the current checks to finish.' : null

  const checkAll = async () => {
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      const r = await checkAllFollows()
      if (alive.current) setNote(r.queued ? `Checking ${r.queued} channel${r.queued === 1 ? '' : 's'}…` : 'All channels were just checked.')
      await load()
    } catch (err) {
      if (alive.current) setError(describeFollowError(err))
    } finally {
      if (alive.current) setBusy(false)
    }
  }

  return (
    <div className="live-check-all">
      <button type="button" className={buttonClass('secondary')} disabled={busy || !!reason}
        aria-describedby={reason ? 'live-check-all-reason' : undefined}
        title={reason ? undefined : `Check ${own ? 'every followed channel' : 'each of your channels'} now, one after another.`}
        onClick={() => void checkAll()}>
        {anyChecking || busy ? 'Checking…' : own ? 'Check all channels' : 'Check all my channels'}
      </button>
      {reason && list.items.length > 0 && <p className="muted" id="live-check-all-reason">{reason}</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {note && <p className="muted" role="status">{note}</p>}
    </div>
  )
}
