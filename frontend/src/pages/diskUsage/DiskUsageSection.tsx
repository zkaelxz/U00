/*
 * Library tools > Data folder: what is taking space in Baihe's data folder (a WizTree-style
 * drill-down), with per-item Move to Trash (restorable from the Trash card in the Storage tab;
 * nothing is freed until deleted from there) and Move for the one folder Baihe can be repointed
 * away from. PC only: away from the PC the tab shows only the PC-only note.
 *
 * Program files (the venv, frontend, .git of a checkout) are not the user's data and have no
 * actions, so they fold into one closed row instead of a card each. The treemap is a mouse aid
 * only (aria-hidden, not focusable): the list under it has the same items with every control.
 */
import { useState } from 'react'

import { moveItem, moveToTrash } from '../../api/diskUsage'
import { Badge } from '../../components/Badge'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'
import { TabCard } from '../libraryTools/TabCard'
import type { DiskUsageItem, DiskUsageScan } from '../../types/diskUsage'
import {
  PARTIAL_TEXT, barPercent, cellLabel, clearBlock, clearConfirmLabel, crumbs, describeCleared, describeMoved,
  diskLine, formatDecimalBytes, itemTone, linkedText, moveBlock, moveConfirmLabel, percentText, scanLinkedText, sizeLine, splitProgramFiles,
  titleSizesText,
} from './diskUsageModel'
import type { DiskUsage } from './useDiskUsage'
import { squarify } from './treemap'
import './diskUsage.css'

const SERVER = { pcOnly: true, serverText: true } as const
// The treemap is laid out in a 100 x 56 box and drawn with percentages.
const BOX_W = 100
const BOX_H = 56

export function DataFolderPanel({ usage }: { usage: DiskUsage }) {
  const { scan, loading, cancelled, error, done, here, setError, load, rescan, changed, cancel } = usage
  const split = scan ? splitProgramFiles(scan.items) : null
  return (
    <TabCard card="data-folder" summary="What is using space in Baihe's data folder">
      <section aria-label="Data folder" className="du" aria-busy={loading}>
        <p className="muted du-intro">
          Only Baihe&apos;s own data folder is shown. Move to Trash puts an item in Baihe&apos;s own Trash
          folder, so you can restore it, but nothing is freed until you delete it from the Trash card in the Storage tab.
        </p>
        <nav aria-label="Folder path" className="du-crumbs">
          <ol>
            {crumbs(here).map((c, i, all) => (
              <li key={c.path || 'root'}>
                {i === all.length - 1 ? (
                  <span aria-current="page">{c.label}</span>
                ) : (
                  <button type="button" className="link" disabled={loading} onClick={() => load(c.path)}>{c.label}</button>
                )}
              </li>
            ))}
          </ol>
        </nav>
        <div className="du-toolbar actions">
          <button type="button" className={buttonClass('secondary', 'sm')} disabled={loading} onClick={() => rescan()}>
            Rescan
          </button>
          {scan?.parent != null && (
            <button type="button" className={buttonClass('ghost', 'sm')} disabled={loading} onClick={() => load(scan.parent ?? '')}>
              Up one level
            </button>
          )}
          {loading && (
            <button type="button" className={buttonClass('ghost', 'sm')} onClick={cancel}>Cancel</button>
          )}
          {scan && <span className="muted du-total">{[`${sizeLine({ size_bytes: scan.total_bytes, file_count: scan.file_count, complete: !scan.partial })} here`, scanLinkedText(scan)].filter(Boolean).join(', ')}</span>}
        </div>
        {scan && diskLine(scan) && <p className="muted du-disk">{diskLine(scan)}</p>}
        {loading && <p className="muted" role="status">Scanning…</p>}
        {cancelled && !loading && <p className="muted" role="status">Scan cancelled. Press Rescan to try again.</p>}
        <ErrorBanner error={error} describe={SERVER} onDismiss={() => setError(null)} />
        {done && <p className="banner ok-banner du-done" role="status">{done}</p>}
        {scan?.partial && scan.partial_reason && (
          <p className="banner warn-banner" role="status">{PARTIAL_TEXT[scan.partial_reason]}</p>
        )}
        {scan?.busy_reason && <p className="muted du-busy">{scan.busy_reason}</p>}
        {scan && scan.not_shown > 0 && <p className="muted du-busy">{`${scan.not_shown.toLocaleString('en-US')} more not shown. Open a smaller folder to see them.`}</p>}
        {scan && !loading && scan.items.length === 0 && !error && <p className="muted">This folder is empty.</p>}
        {scan && split && scan.items.length > 0 && (
          <>
            <Treemap scan={scan} onOpen={(p) => load(p)} />
            <ul className="du-list" aria-label={`Items in ${crumbs(here).at(-1)?.label ?? 'folder'}`}>
              {split.own.map((item) => (
                <ItemRow
                  key={item.path}
                  item={item}
                  scan={scan}
                  onOpen={() => load(item.path)}
                  onChanged={changed}
                  onStale={() => rescan(true)}
                />
              ))}
            </ul>
            <ProgramFiles items={split.program} bytes={split.programBytes} />
          </>
        )}
      </section>
    </TabCard>
  )
}

// One closed row instead of a card per item: these have no actions, only a reason.
function ProgramFiles({ items, bytes }: { items: DiskUsageItem[]; bytes: number }) {
  if (items.length === 0) return null
  return (
    <details className="du-program">
      <summary>
        <span className="du-name">Program files (protected)</span>
        <span className="du-size num">{formatDecimalBytes(bytes)}</span>
      </summary>
      <p className="muted du-why">Part of the program, not your data. They can&apos;t be moved or cleared from here.</p>
      <ul className="du-program-list">
        {items.map((i) => (
          <li key={i.path}><span>{i.name}</span> <span className="muted num">{sizeLine(i)}</span></li>
        ))}
      </ul>
    </details>
  )
}

function Treemap({ scan, onOpen }: { scan: DiskUsageScan; onOpen: (path: string) => void }) {
  const rects = squarify(scan.items.map((i) => ({ id: i.path, value: i.size_bytes })), BOX_W, BOX_H)
  const byPath = new Map(scan.items.map((i) => [i.path, i]))
  if (!rects.length) return null
  return (
    <div className="du-treemap" aria-hidden="true" data-testid="du-treemap">
      {rects.map((r) => {
        const item = byPath.get(r.id)
        if (!item) return null
        const label = cellLabel(item, r.w, r.h)
        return (
          <button
            key={r.id}
            type="button"
            tabIndex={-1}
            className={`du-cell du-tone-${itemTone(item)}`}
            style={{ left: `${r.x}%`, top: `${(r.y / BOX_H) * 100}%`, width: `${r.w}%`, height: `${(r.h / BOX_H) * 100}%` }}
            title={`${item.name}: ${sizeLine(item)}`}
            disabled={item.kind !== 'folder'}
            onClick={() => onOpen(item.path)}
          >
            <span className="du-cell-label">{label}</span>
          </button>
        )
      })}
    </div>
  )
}

function ItemRow({ item, scan, onOpen, onChanged, onStale }: {
  item: DiskUsageItem
  scan: DiskUsageScan
  onOpen: () => void
  onChanged: (message: string) => void
  onStale: () => void
}) {
  const [busy, setBusy] = useState<'clear' | 'move' | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [ack, setAck] = useState(false)
  const [moving, setMoving] = useState(false)
  const [dest, setDest] = useState('')
  const reasonId = `du-why-${item.path.replace(/[^A-Za-z0-9_-]/g, '_')}`
  const clearWhy = clearBlock(item, scan)
  const moveWhy = moveBlock(item, scan)
  const tone = itemTone(item)
  const isBackups = /^[^/]+\/backups(\/|$)/.test(item.path)

  const fail = (e: unknown) => {
    setBusy(null)
    setError(e)
    // A 409 means the item changed or a job started: show the new state.
    if ((e as { status?: number } | null)?.status === 409) onStale()
  }
  const clear = () => {
    setBusy('clear')
    setError(null)
    moveToTrash(item).then(
      (r) => { setBusy(null); onChanged(describeCleared(r)) },
      fail,
    )
  }
  const move = () => {
    setBusy('move')
    setError(null)
    moveItem(item.path, dest.trim()).then(
      (r) => { setBusy(null); setMoving(false); onChanged(describeMoved(r)) },
      fail,
    )
  }
  // A title's own media needs the extra tick before the two-step confirm.
  const needsAck = item.irreplaceable && !ack

  return (
    <li className={`du-row du-tone-${tone}`}>
      <div className="du-row-main">
        {item.kind === 'folder' && !item.is_link ? (
          <button type="button" className="du-name link" onClick={onOpen} aria-label={`Open ${item.name}`}>
            {item.name}
          </button>
        ) : (
          <span className="du-name">{item.name}</span>
        )}
        <span className="du-size num">{sizeLine(item)}</span>
        <span className="du-pct muted num">{percentText(item.percent_of_parent)}</span>
      </div>
      {linkedText(item) && <p className="muted du-why du-linked">{linkedText(item)}</p>}
      {titleSizesText(item.title_sizes) && <p className="muted du-why du-kinds">{titleSizesText(item.title_sizes)}</p>}
      <div className="du-bar" aria-hidden="true">
        <span style={{ width: `${barPercent(item)}%` }} />
      </div>
      <div className="du-tags">
        {item.protected && <Badge tone="neutral">Protected</Badge>}
        {item.irreplaceable && <Badge tone="warn">Can&apos;t be recreated</Badge>}
        {item.regenerable && <Badge tone="ok" title={item.regenerable.note}>{item.regenerable.label}: rebuilt by Baihe</Badge>}
        {item.movable.supported && <Badge tone="info">Movable</Badge>}
      </div>
      {item.protected && <p className="muted du-why" id={reasonId}>{item.protected_reason}</p>}
      {item.irreplaceable && item.irreplaceable_note && !item.protected && <p className="du-why du-warn" id={reasonId}>{item.irreplaceable_note}</p>}
      {!item.protected && clearWhy && <p className="muted du-why" id={reasonId}>{clearWhy}</p>}
      {!item.protected && !item.movable.supported && moveWhy && (
        <p className="muted du-why">Can&apos;t be moved: {moveWhy}</p>
      )}
      <div className="du-actions">
        {item.irreplaceable && !item.protected && (
          <label className="du-ack">
            <input type="checkbox" checked={ack} disabled={!!clearWhy || busy !== null} onChange={(e) => setAck(e.target.checked)} />
            <span>{isBackups ? `I understand ${item.name} holds backups and can't be recreated` : `I understand ${item.name} is source media and can't be recreated`}</span>
          </label>
        )}
        <ConfirmButton
          name={item.name}
          label="Move to Trash…"
          ariaLabel={`Move ${item.name} to Trash`}
          confirmLabel={clearConfirmLabel(item)}
          verb="move to Trash"
          busy={busy === 'clear'}
          disabled={!!clearWhy || needsAck || busy === 'move'}
          describedBy={clearWhy || needsAck ? reasonId : undefined}
          onConfirm={clear}
        />
        {item.movable.supported && !moveWhy && !moving && (
          <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy !== null} onClick={() => setMoving(true)}>
            Move…
          </button>
        )}
      </div>
      {moving && (
        <form className="du-move" onSubmit={(e) => e.preventDefault()}>
          <Field label={`New folder for ${item.name}`}>
            <input value={dest} onChange={(e) => setDest(e.target.value)} placeholder="D:\Baihe backups" autoFocus />
          </Field>
          <p className="muted du-why">
            A full folder path on this PC that already exists, outside Baihe&apos;s data folder. Baihe moves its
            backup copies there and keeps using it.
          </p>
          <div className="actions">
            <ConfirmButton
              name={item.name}
              label="Move…"
              ariaLabel={`Move ${item.name}`}
              confirmLabel={moveConfirmLabel(item)}
              verb="move"
              tone="primary"
              busy={busy === 'move'}
              disabled={!dest.trim()}
              onConfirm={move}
            />
            <button type="button" className={buttonClass('ghost', 'sm')} disabled={busy === 'move'} onClick={() => setMoving(false)}>Cancel</button>
          </div>
        </form>
      )}
      <ErrorBanner error={error} describe={SERVER} onDismiss={() => setError(null)} />
    </li>
  )
}
