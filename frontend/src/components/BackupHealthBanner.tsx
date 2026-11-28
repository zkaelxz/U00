/*
 * BackupHealthBanner: in the app shell, on the main PC only (usePcOnly 'local'; the backup routes are PC only),
 * while the last automatic backup failed, none has succeeded for a long while, or the copies share the
 * library's drive (GET /api/backups/settings `problem`). Dismissing hides it for this page load only, so a lasting problem
 * comes back after a reload; the one-time same-drive note is dismissed on the server, for good.
 * A refusal or a server without the route stops the reads.
 */
import { useEffect, useState } from 'react'

import { ApiError } from '../api/client'
import { dismissSameDriveNote, getBackupSettings } from '../api/backups'
import { usePcOnly } from '../hooks/usePcOnly'
import { routeHref } from '../router'
import { BACKUP_PROBLEM_TEXT } from '../pages/backupsFormat'
import type { AutoBackupSettings } from '../types/backups'
import { WarnBar } from './WarnBar'

export const POLL_MS = 10 * 60_000
const HIDE_ON = [401, 403, 404]

export function BackupHealthBanner() {
  const pc = usePcOnly()
  const [problem, setProblem] = useState<AutoBackupSettings['problem']>(null)
  const [dismissed, setDismissed] = useState<AutoBackupSettings['problem']>(null)
  useEffect(() => {
    if (pc !== 'local') return
    let live = true
    let timer: ReturnType<typeof setInterval> | undefined
    const load = () =>
      getBackupSettings().then(
        (s) => {
          if (live) setProblem(s.problem ?? null)
        },
        (e: unknown) => {
          if (e instanceof ApiError && HIDE_ON.includes(e.status)) {
            if (live) setProblem(null)
            if (timer) clearInterval(timer)
          }
        },
      )
    void load()
    timer = setInterval(() => void load(), POLL_MS)
    return () => {
      live = false
      if (timer) clearInterval(timer)
    }
  }, [pc])
  if (pc !== 'local' || !problem || problem === dismissed) return null
  const text = BACKUP_PROBLEM_TEXT[problem]
  return (
    <WarnBar
      shell
      testId="backup-health-banner"
      title={text}
      action={<a href={routeHref({ name: 'library-tools', tab: 'backup' })}>Backups</a>}
      onDismiss={() => {
        setDismissed(problem)
        if (problem === 'same_drive') void dismissSameDriveNote().catch(() => undefined)
      }}
    >
      {text}
    </WarnBar>
  )
}
