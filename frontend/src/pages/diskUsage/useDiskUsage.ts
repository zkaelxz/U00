import { useCallback, useEffect, useRef, useState } from 'react'

import { listTrash, listUnusedVoiceClips, scanDiskUsage } from '../../api/diskUsage'
import type { DiskUsageScan, DiskUsageTrashList, UnusedVoiceClipList } from '../../types/diskUsage'

/**
 * The scan, Trash list and unused-clip list behind the Storage and Data folder tabs. They share one
 * state because a Trash move in one tab changes the numbers in the other. Nothing is requested until
 * `wanted` first turns true (a tab that uses it is shown), and every scan walks the disk afresh.
 */
export function useDiskUsage(wanted: boolean) {
  const [scan, setScan] = useState<DiskUsageScan | null>(null)
  // The drive summary needs the data folder's own total, which a scan of a subfolder doesn't carry.
  const [rootBytes, setRootBytes] = useState<number | null>(null)
  const [trash, setTrash] = useState<DiskUsageTrashList | null>(null)
  const [clips, setClips] = useState<UnusedVoiceClipList | null>(null)
  const [loading, setLoading] = useState(false)
  const [cancelled, setCancelled] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [done, setDone] = useState<string | null>(null)
  const started = useRef(false)
  const abort = useRef<AbortController | null>(null)
  const latest = useRef(0)
  const latestTrash = useRef(0)
  const latestClips = useRef(0)

  // A new scan aborts the previous one, and the ticket drops the aborted scan's late answer so it cannot
  // overwrite the newer scan or set `cancelled`. Only the user's Cancel aborts without bumping the ticket,
  // so it is the only abort that reaches setCancelled.
  const load = useCallback((path: string, keepNotice = false) => {
    abort.current?.abort()
    const ctl = new AbortController()
    abort.current = ctl
    const ticket = ++latest.current
    setLoading(true)
    setCancelled(false)
    setError(null)
    if (!keepNotice) setDone(null)
    scanDiskUsage(path, ctl.signal).then(
      (s) => {
        if (ticket !== latest.current) return
        setScan(s)
        if (s.path === '') setRootBytes(s.total_bytes)
        setLoading(false)
      },
      (e: unknown) => {
        if (ticket !== latest.current) return
        setLoading(false)
        if (ctl.signal.aborted) setCancelled(true)
        else setError(e)
      },
    )
  }, [])

  // The Trash list is its own request and never waits for or fails with the scan; only the newest answer counts.
  const loadTrash = useCallback(() => {
    const ticket = ++latestTrash.current
    listTrash().then(
      (t) => { if (ticket === latestTrash.current) setTrash(t) },
      () => {
        // The scan's own error banner covers a broken connection; the list just stays as it was.
      },
    )
  }, [])

  const loadClips = useCallback(() => {
    const ticket = ++latestClips.current
    listUnusedVoiceClips().then(
      (l) => { if (ticket === latestClips.current) setClips(l) },
      () => {
        // Like the Trash list: the scan's banner covers a broken connection; the list stays as it was.
      },
    )
  }, [])

  useEffect(() => {
    if (!wanted || started.current) return
    started.current = true
    load('')
    loadTrash()
    loadClips()
  }, [wanted, load, loadTrash, loadClips])

  const here = scan?.path ?? ''
  const rescan = (keepNotice = false) => {
    load(here, keepNotice)
    loadTrash()
    loadClips()
  }
  const changed = (msg: string) => {
    setDone(msg)
    rescan(true)
  }

  return {
    scan, rootBytes, trash, clips, loading, cancelled, error, done, here,
    setError, load, rescan, changed, cancel: () => abort.current?.abort(),
  }
}

export type DiskUsage = ReturnType<typeof useDiskUsage>
