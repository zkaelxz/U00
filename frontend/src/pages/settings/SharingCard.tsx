/*
 * Settings > Sharing: who in the household can see each drama and series.
 * Everyone signed in (and the owner at the PC with sign-in off) gets the
 * "share new items" switch, off by default. Admins also get every item with
 * its owner and a Shared/Private switch; the server allows a flip only by
 * the owner or an admin and explains a refusal (409) in plain words, which
 * is shown as is. Other people's per-item switches live only here, for admins.
 * Admins also pick each item's owner (an account or the PC); away from the PC
 * the server allows that, like a flip, only for their own and PC-made items.
 * Items with no owner (made at the PC or with sign-in off) are stored private,
 * so the list notes that, badges them and can filter to the private ones.
 */
import { useEffect, useState } from 'react'

import { listAdminUsers } from '../../api/adminUsers'
import { getShareByDefault, listSharing, setItemOwner, setItemPrivate, setShareByDefault } from '../../api/sharing'
import { ButtonLink } from '../../components/Button'
import { Card } from '../../components/Card'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import { badgeClass, buttonClass } from '../../components/uiClasses'
import { REMOTE_ADMIN_NOTE, isRemoteAdmin, recheckSession, useSession } from '../../hooks/useSession'
import { routeHref } from '../../router'
import type { SharingItem } from '../../types/sharing'
import {
  ACCOUNT_UNAVAILABLE_NOTE,
  CHECKING_ACCOUNT_NOTE,
  PC_ITEMS_NOTE,
  SHARE_DEFAULT_LABEL,
  applyFlip,
  applyOwner,
  canChangeOwners,
  canSeeAllItems,
  filterPcPrivate,
  followsSeries,
  isSharingForbidden,
  isShared,
  itemKey,
  itemTitle,
  mergePage,
  seriesNote,
  shareDefaultHelp,
  sharingErrorText,
  sharingStatusLabel,
} from './sharing'

const TITLE = 'Sharing'
const PAGE = 100

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
      {admin && <SharingItems owners={canChangeOwners(session)} />}
    </Card>
  )
}

type Owner = { id: number; name: string }

/** Like the server's owner names: never an email, even one typed in as a display name. */
function ownerLabel(id: number, displayName: string): string {
  const name = displayName.trim()
  return name && !name.includes('@') ? name : `User ${id}`
}

function SharingItems({ owners }: { owners: boolean }) {
  const [items, setItems] = useState<SharingItem[] | null>(null)
  const [people, setPeople] = useState<Owner[]>([])
  const [total, setTotal] = useState(0)
  const [hidden, setHidden] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [listError, setListError] = useState<string | null>(null)
  const [busyKey, setBusyKey] = useState<string | null>(null)
  const [rowError, setRowError] = useState<{ key: string; text: string } | null>(null)
  const [pcOnly, setPcOnly] = useState(false)

  useEffect(() => {
    let live = true
    listSharing(0, PAGE).then(
      (r) => {
        if (!live) return
        setItems(r.items)
        setTotal(r.total)
      },
      (e: unknown) => {
        if (!live) return
        if (isSharingForbidden(e)) setHidden(true)
        else setListError(sharingErrorText(e))
      },
    )
    return () => {
      live = false
    }
  }, [])

  useEffect(() => {
    if (!owners) return
    let live = true
    // Without the list the picker only offers the current owner and the PC.
    listAdminUsers().then(
      (r) => live && setPeople(r.users.filter((u) => u.is_active).map((u) => ({ id: u.id, name: ownerLabel(u.id, u.display_name) }))),
      () => undefined,
    )
    return () => {
      live = false
    }
  }, [owners])

  if (hidden) return null

  const changeOwner = (item: SharingItem, value: string) => {
    const key = itemKey(item)
    setBusyKey(key)
    setRowError(null)
    setItemOwner(item.kind, item.id, value === 'pc' ? null : Number(value)).then(
      (r) => {
        setItems((cur) => (cur ? applyOwner(cur, r) : cur))
        setBusyKey(null)
      },
      (e: unknown) => {
        setRowError({ key, text: sharingErrorText(e) })
        setBusyKey(null)
      },
    )
  }

  const more = () => {
    if (!items) return
    setLoadingMore(true)
    setListError(null)
    listSharing(items.length, PAGE).then(
      (r) => {
        setItems((cur) => mergePage(cur ?? [], r.items))
        setTotal(r.total)
        setLoadingMore(false)
      },
      (e: unknown) => {
        setListError(sharingErrorText(e))
        setLoadingMore(false)
      },
    )
  }

  const flip = (item: SharingItem, shared: boolean) => {
    const key = itemKey(item)
    setBusyKey(key)
    setRowError(null)
    setItemPrivate(item.kind, item.id, !shared).then(
      (r) => {
        setItems((cur) => (cur ? applyFlip(cur, r) : cur))
        setBusyKey(null)
      },
      (e: unknown) => {
        setRowError({ key, text: sharingErrorText(e) })
        setBusyKey(null)
      },
    )
  }

  return (
    <div className="settings-group">
      <h2 className="settings-subhead subhead">Every title and series</h2>
      <p className="settings-note">
        Shared: everyone in the household can see it. Private: only its owner and admins can. A series decides for
        all of its titles.
      </p>
      <p className="settings-note" data-testid="sharing-pc-note">
        {PC_ITEMS_NOTE}
      </p>
      {items !== null && items.length > 0 && (
        <div className="setting-list">
          <Field label="Show only private items created at the PC">
            <Toggle checked={pcOnly} onChange={setPcOnly} />
          </Field>
        </div>
      )}
      {items === null ? (
        !listError && <p className="muted">Loading…</p>
      ) : items.length === 0 ? (
        <div className="settings-actions">
          <span className="muted">There are no titles or series yet.</span>
          <ButtonLink size="sm" href={routeHref({ name: 'library' })}>Go to Library</ButtonLink>
        </div>
      ) : pcOnly && filterPcPrivate(items).length === 0 ? (
        <p className="muted">No private items created at the PC{items.length < total ? ' in the items shown so far' : ''}.</p>
      ) : (
        <ul className="status-list sharing-list" aria-label="Titles and series">
          {(pcOnly ? filterPcPrivate(items) : items).map((item) => {
            const key = itemKey(item)
            const title = itemTitle(item)
            const kindLabel = item.kind === 'series' ? 'Series' : 'Title'
            return (
              <li key={key} className={followsSeries(item) ? 'sharing-in-series' : undefined} data-testid={`sharing-${key}`}>
                <div className="status-row sharing-row">
                  <span className="status-row-name">
                    {title}
                    <span className="settings-note sharing-meta">
                      {kindLabel} · Owner: {item.owner_name}
                    </span>
                    {item.created_at_pc && <span className={badgeClass('neutral')}>Created at the PC</span>}
                  </span>
                  {owners && (
                    <select
                      aria-label={`Owner of ${kindLabel.toLowerCase()} “${title}”`}
                      value={item.owner_user_id == null ? 'pc' : String(item.owner_user_id)}
                      disabled={busyKey === key}
                      onChange={(e) => changeOwner(item, e.target.value)}
                    >
                      <option value="pc">PC owner</option>
                      {item.owner_user_id != null && !people.some((p) => p.id === item.owner_user_id) && (
                        <option value={String(item.owner_user_id)}>{item.owner_name}</option>
                      )}
                      {people.map((p) => (
                        <option key={p.id} value={String(p.id)}>{p.name}</option>
                      ))}
                    </select>
                  )}
                  <span className="sharing-status">{sharingStatusLabel(item)}</span>
                  {!followsSeries(item) && (
                    <Toggle
                      checked={isShared(item)}
                      disabled={busyKey === key}
                      aria-label={`Share ${kindLabel.toLowerCase()} “${title}” with the household`}
                      onChange={(next) => flip(item, next)}
                    />
                  )}
                </div>
                {followsSeries(item) && <p className="settings-note">{seriesNote(item)}</p>}
                {rowError?.key === key && (
                  <p className="error" role="alert">
                    {rowError.text}
                  </p>
                )}
              </li>
            )
          })}
        </ul>
      )}
      {listError && (
        <p className="error" role="alert">
          {listError}
        </p>
      )}
      {items !== null && items.length < total && (
        <div className="settings-actions">
          <button type="button" className={buttonClass('secondary', 'sm')} disabled={loadingMore} onClick={more}>
            {loadingMore ? 'Loading…' : `Show more (${total - items.length} left)`}
          </button>
        </div>
      )}
    </div>
  )
}
