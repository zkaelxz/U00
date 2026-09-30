import { useState } from 'react'

import { setItemPrivate } from '../api/sharing'
import { useSession } from '../hooks/useSession'
import type { SharingKind } from '../types/sharing'
import { Badge } from './Badge'
import { FOLLOWS_SERIES_NOTE, badgeTitle, flipSharing, sharingView } from './sharingControl'
import { buttonClass } from './uiClasses'

interface Props {
  kind: SharingKind
  id: number
  title: string
  isPrivate?: boolean | null
  ownedByMe?: boolean | null
  seriesId?: number | null
}

// Sharing state of one item the viewer owns: a badge and a one-tap switch.
// The server decides what is allowed; its refusal (409) is shown as is.
export function SharingControl({ kind, id, title, isPrivate, ownedByMe, seriesId }: Props) {
  const session = useSession()
  // The confirmed flip, kept only while the list still carries the value it was made from.
  const [flipped, setFlipped] = useState<{ from: boolean | null | undefined; to: boolean } | null>(null)
  const priv = flipped && flipped.from === isPrivate ? flipped.to : isPrivate
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const view = sharingView(session, { kind, is_private: priv, owned_by_me: ownedByMe, series_id: seriesId })
  if (!view.show) return null

  const flip = () => {
    setBusy(true)
    setError(null)
    void flipSharing(setItemPrivate, kind, id, priv === true).then((r) => {
      if ('error' in r) setError(r.error)
      else setFlipped({ from: isPrivate, to: r.isPrivate })
      setBusy(false)
    })
  }

  return (
    <span className="sharing-control">
      <Badge tone={view.label === 'Shared' ? 'ok' : 'neutral'} title={badgeTitle(view.label)}>{view.label}</Badge>
      {view.follows ? (
        <span className="muted sharing-follows">{FOLLOWS_SERIES_NOTE}</span>
      ) : (
        <button
          type="button"
          className={buttonClass('ghost', 'sm')}
          disabled={busy}
          onClick={flip}
          aria-label={`${view.actionLabel}: ${title}`}
        >
          {view.actionLabel}
        </button>
      )}
      {error && <span className="error sharing-error" role="alert">{error}</span>}
    </span>
  )
}
