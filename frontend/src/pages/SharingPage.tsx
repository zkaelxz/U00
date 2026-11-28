/*
 * The Sharing page (#/sharing, from Settings > Sharing): every drama and series with its owner and a Shared/Private
 * switch, for admins (and the owner at the PC with sign-in off). The server allows a flip only by the owner or an
 * admin and explains a refusal (409) in plain words, which is shown as is. Admins also pick each item's owner (an
 * account or the PC) once there is an account to pick, since a lone "PC owner" choice changes nothing; away from the
 * PC the server allows that, like a flip, only for their own and PC-made items. Items with no owner (made at the PC
 * or with sign-in off) are stored private, so the page notes that, badges them and can filter to them.
 *
 * Every item is loaded up front so the order and the owner filter cover the whole library, not the rows fetched so
 * far; the list scrolls inside its box so the controls stay in view.
 */
import { useEffect, useState } from 'react'

import { listAdminUsers } from '../api/adminUsers'
import { listSharing, setItemOwner, setItemPrivate } from '../api/sharing'
import { ButtonLink } from '../components/Button'
import { PageShell } from '../components/PageShell'
import { Toggle } from '../components/Toggle'
import { badgeClass, buttonClass } from '../components/uiClasses'
import { REMOTE_ADMIN_NOTE, isRemoteAdmin, recheckSession, useSession } from '../hooks/useSession'
import { Breadcrumbs } from '../nav/BreadcrumbNav'
import { routeCrumbs } from '../nav/breadcrumbs'
import { routeHref } from '../router'
import type { SharingItem } from '../types/sharing'
import {
  ACCOUNT_UNAVAILABLE_NOTE,
  CHECKING_ACCOUNT_NOTE,
  PC_ITEMS_NOTE,
  SHARING_ORDERS,
  SHARING_SHOWS,
  type SharingOrder,
  type SharingShow,
  applyFlip,
  applyOwner,
  arrangeSharing,
  canChangeOwners,
  canSeeAllItems,
  followsSeries,
  isSharingForbidden,
  isShared,
  itemKey,
  itemTitle,
  mergePage,
  ownerChoices,
  seriesNote,
  sharingErrorText,
  sharingStatusLabel,
  showOwnerPicker,
} from './settings/sharing'
import './settings/settings.css'

// The server's largest page, so a few hundred items take two or three requests.
const PAGE = 200

export default function SharingPage() {
  const session = useSession()
  const admin = canSeeAllItems(session)
  return (
    <PageShell
      width="standard"
      title="Sharing"
      label="Sharing"
      meta="Who in the household can see each title and series, and who owns it."
      className="settings-page sharing-page"
      back={<Breadcrumbs crumbs={routeCrumbs({ name: 'sharing' })} />}
    >
      {session.status === 'loading' && <p className="muted" role="status">{CHECKING_ACCOUNT_NOTE}</p>}
      {session.status === 'unavailable' && (
        <div className="settings-actions">
          <p className="muted" role="status">{ACCOUNT_UNAVAILABLE_NOTE}</p>
          <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => void recheckSession()}>
            Retry
          </button>
        </div>
      )}
      {admin === false && session.status === 'ready' && <NotAdmin />}
      {admin && isRemoteAdmin(session) && (
        <p className="settings-note" data-testid="remote-admin-sharing-note">
          {REMOTE_ADMIN_NOTE} Here you change your own items and the ones made at the PC.
        </p>
      )}
      {admin && <SharingItems owners={canChangeOwners(session)} />}
    </PageShell>
  )
}

function NotAdmin() {
  return (
    <div className="settings-actions">
      <span className="muted">Only an admin can see and change every item. Your own switch for new items is in Settings.</span>
      <ButtonLink size="sm" href={routeHref({ name: 'settings' })}>Go to Settings</ButtonLink>
    </div>
  )
}

type Owner = { id: number; name: string }

/** Like the server's owner names: never an email, even one typed in as a display name. */
function ownerLabel(id: number, displayName: string): string {
  const name = displayName.trim()
  return name && !name.includes('@') ? name : `User ${id}`
}

/** Every page of the list, in the server's order. */
async function loadAll(live: () => boolean): Promise<{ items: SharingItem[]; total: number }> {
  let items: SharingItem[] = []
  for (;;) {
    const r = await listSharing(items.length, PAGE)
    if (!live()) return { items, total: r.total }
    const before = items.length
    items = mergePage(items, r.items)
    // Rows can shift between pages; an empty or all-seen page ends the loop rather than asking forever.
    if (items.length >= r.total || items.length === before) return { items, total: r.total }
  }
}

function SharingItems({ owners }: { owners: boolean }) {
  const [items, setItems] = useState<SharingItem[] | null>(null)
  const [people, setPeople] = useState<Owner[]>([])
  const [hidden, setHidden] = useState(false)
  const [short, setShort] = useState(false)
  const [listError, setListError] = useState<string | null>(null)
  const [busyKey, setBusyKey] = useState<string | null>(null)
  const [rowError, setRowError] = useState<{ key: string; text: string } | null>(null)
  const [order, setOrder] = useState<SharingOrder>('status')
  const [show, setShow] = useState<SharingShow>('all')
  const [owner, setOwner] = useState('')

  useEffect(() => {
    let live = true
    loadAll(() => live).then(
      (all) => {
        if (!live) return
        setItems(all.items)
        setShort(all.items.length < all.total)
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

  const renderRow = (item: SharingItem, seriesShown: ReadonlySet<number>) => {
    const key = itemKey(item)
    const title = itemTitle(item)
    const kindLabel = item.kind === 'series' ? 'Series' : 'Title'
    // Indented only under its own series row; a filter or the owner order can leave a title without it.
    const nested = order === 'status' && followsSeries(item) && seriesShown.has(item.series_id ?? -1)
    return (
      <li key={key} className={nested ? 'sharing-in-series' : undefined} data-testid={`sharing-${key}`}>
        <div className="status-row sharing-row">
          <span className="status-row-name">
            {title}
            <span className="settings-note sharing-meta">
              {kindLabel} · Owner: {item.owner_name}
            </span>
            {item.created_at_pc && <span className={badgeClass('neutral')}>Created at the PC</span>}
          </span>
          {owners && showOwnerPicker(item, people) && (
            <select
              className="sharing-owner"
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
  }

  const choices = items ? ownerChoices(items) : []
  // An owner change can take an owner's last item; the filter then falls back to everyone rather than an empty list.
  const ownerShown = choices.some((c) => c.value === owner) ? owner : ''
  const groups = items ? arrangeSharing(items, order, show, ownerShown) : []
  return (
    <div className="settings-group">
      <p className="settings-note">
        Shared: everyone in the household can see it. Private: only its owner and admins can. A series decides for
        all of its titles.
      </p>
      <p className="settings-note" data-testid="sharing-pc-note">
        {PC_ITEMS_NOTE}
      </p>
      {items !== null && items.length > 0 && (
        <div className="sharing-controls">
          <label>
            Order
            <select value={order} onChange={(e) => setOrder(e.target.value as SharingOrder)}>
              {SHARING_ORDERS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label>
          <label>
            Show
            <select value={show} onChange={(e) => setShow(e.target.value as SharingShow)}>
              {SHARING_SHOWS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label>
          {choices.length > 1 && (
            <label>
              Owner
              <select value={ownerShown} onChange={(e) => setOwner(e.target.value)}>
                <option value="">Everyone</option>
                {choices.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </label>
          )}
        </div>
      )}
      {items === null ? (
        !listError && <p className="muted">Loading…</p>
      ) : items.length === 0 ? (
        <div className="settings-actions">
          <span className="muted">There are no titles or series yet.</span>
          <ButtonLink size="sm" href={routeHref({ name: 'library' })}>Go to Library</ButtonLink>
        </div>
      ) : groups.length === 0 ? (
        <p className="muted">Nothing matches. Change Show or Owner to see more.</p>
      ) : (
        <div className="scroll-box sharing-box" role="group" aria-label="Titles and series" tabIndex={0}>
          {groups.map((group) => (
            <div key={group.key}>
              <h2 className="subhead">{group.label} ({group.items.length})</h2>
              <ul className="status-list sharing-list" aria-label={`${group.label} titles and series`}>
                {group.items.map((i) => renderRow(i, new Set(group.items.filter((x) => x.kind === 'series').map((x) => x.id))))}
              </ul>
            </div>
          ))}
        </div>
      )}
      {short && (
        <p className="settings-note" role="status">
          Some items changed while this list loaded, so it may be missing a few. Reload the page to see them all.
        </p>
      )}
      {listError && (
        <p className="error" role="alert">
          {listError}
        </p>
      )}
    </div>
  )
}
