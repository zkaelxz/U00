/*
 * Settings > Sharing: the caller's own "share new items" switch, off by default, for everyone signed in (and the
 * owner at the PC with sign-in off). Admins get a link to the Sharing page (SharingPage.tsx), which lists every
 * item; it is a page of its own because a household library is hundreds of rows.
 */
import { useEffect, useState } from 'react'

import { getShareByDefault, setShareByDefault } from '../../api/sharing'
import { ButtonLink } from '../../components/Button'
import { Card } from '../../components/Card'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import { buttonClass } from '../../components/uiClasses'
import { REMOTE_ADMIN_NOTE, isRemoteAdmin, recheckSession, useSession } from '../../hooks/useSession'
import { routeHref } from '../../router'
import {
  ACCOUNT_UNAVAILABLE_NOTE,
  CHECKING_ACCOUNT_NOTE,
  SHARE_DEFAULT_LABEL,
  canSeeAllItems,
  shareDefaultHelp,
  sharingErrorText,
} from './sharing'

const TITLE = 'Sharing'

export function SharingCard() {
  const session = useSession()
  const admin = canSeeAllItems(session)
  const remoteAdmin = isRemoteAdmin(session)
  const [shareDefault, setShareDefault] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    getShareByDefault().then(
      (r) => live && setShareDefault(r.share_by_default),
      (e: unknown) => live && setError(sharingErrorText(e)),
    )
    return () => {
      live = false
    }
  }, [])

  const flipDefault = (next: boolean) => {
    setBusy(true)
    setError(null)
    setShareByDefault(next).then(
      (r) => {
        setShareDefault(r.share_by_default)
        setBusy(false)
      },
      (e: unknown) => {
        setError(sharingErrorText(e))
        setBusy(false)
      },
    )
  }

  if (session.status !== 'ready') {
    return (
      <Card title={TITLE} aria-label={TITLE} className="sharing-card">
        {session.status === 'loading' ? (
          <p className="muted" role="status">{CHECKING_ACCOUNT_NOTE}</p>
        ) : (
          <div className="settings-actions">
            <p className="muted" role="status">{ACCOUNT_UNAVAILABLE_NOTE}</p>
            <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => void recheckSession()}>
              Retry
            </button>
          </div>
        )}
      </Card>
    )
  }

  return (
    <Card
      title={TITLE}
      aria-label={TITLE}
      className="sharing-card"
      meta={shareDefault === null ? undefined : shareDefault ? 'New items shared with the household' : 'New items private'}
    >
      {shareDefault === null ? (
        !error && <p className="muted">Loading…</p>
      ) : (
        <div className="settings-group">
          <div className="setting-list">
            <Field label={SHARE_DEFAULT_LABEL}>
              <Toggle checked={shareDefault} disabled={busy} onChange={flipDefault} />
            </Field>
          </div>
          <p className="settings-note" data-testid="share-default-help">
            {shareDefaultHelp(shareDefault, admin === true)}
          </p>
        </div>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {remoteAdmin && (
        <p className="settings-note" data-testid="remote-admin-sharing-note">
          {REMOTE_ADMIN_NOTE} Here you change your own items and the ones made at the PC.
        </p>
      )}
      {admin && (
        <div className="settings-actions">
          <span className="settings-note">Who can see each title and series, and who owns it.</span>
          <ButtonLink size="sm" href={routeHref({ name: 'sharing' })}>Open the Sharing page</ButtonLink>
        </div>
      )}
    </Card>
  )
}
