/*
 * Settings > Sharing: who in the household can see each drama and series.
 * Everyone signed in (and the owner at the PC with sign-in off) gets the
 * "share new items" switch, off by default. Admins also get every item with
 * its owner and a Shared/Private switch; the server allows a flip only by
 * the owner or an admin and explains a refusal (409) in plain words, which
 * is shown as is. Other people's per-item switches live only here, for admins.
 * Items with no owner (made at the PC or with sign-in off) are stored private,
 * so the list notes that, badges them and can filter to the private ones.
 */
import { useEffect, useState } from 'react'

import { getShareByDefault, listSharing, setItemPrivate, setShareByDefault } from '../../api/sharing'
import { Card } from '../../components/Card'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import { badgeClass, buttonClass } from '../../components/uiClasses'
import { REMOTE_ADMIN_NOTE, isRemoteAdmin, useSession } from '../../hooks/useSession'
import type { SharingItem } from '../../types/sharing'
import {
  PC_ITEMS_NOTE,
  SHARE_DEFAULT_LABEL,
  applyFlip,
  canSeeAllItems,
  filterPcPrivate,
  followsSeries,
  isForbidden,
  isShared,
  itemKey,
  itemTitle,
  mergePage,
  seriesNote,
  shareDefaultHelp,
  sharingErrorText,
  statusLabel,
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

  return (
    <Card title={TITLE} aria-label={TITLE} className="sharing-card">
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
      {admin && <SharingItems />}
      {remoteAdmin && (
        <p className="settings-note" data-testid="remote-admin-sharing-note">
          {REMOTE_ADMIN_NOTE} Sharing for your own items is set from the Library.
        </p>
      )}
    </Card>
  )
}

function SharingItems() {
  const [items, setItems] = useState<SharingItem[] | null>(null)
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
        if (isForbidden(e)) setHidden(true)
        else setListError(sharingErrorText(e))
      },
    )
    return () => {
      live = false
    }
  }, [])

  if (hidden) return null

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
      <h4 className="settings-subhead">Every title and series</h4>
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
        <p className="muted">There are no titles or series yet.</p>
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
                  <span className="sharing-status">{statusLabel(item)}</span>
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
