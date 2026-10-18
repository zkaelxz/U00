/*
 * Settings > Automatic backups: only the current summary and a link. The schedule, folder, "Back up now" and
 * the copies live in Library tools > Backup, next to Restore, which is the only place a title is restored from a copy.
 * PC only (PC_ONLY_CARDS), so nothing is fetched until /api/meta has answered.
 */
import { useEffect, useState } from 'react'

import { getBackupSettings } from '../../api/backups'
import { getPcMode, loadPcMode } from '../../api/pcOnly'
import { ButtonLink } from '../../components/Button'
import { Card } from '../../components/Card'
import { routeHref } from '../../router'
import type { AutoBackupSettings } from '../../types/backups'
import { settingsSummary } from '../backupsFormat'

const TITLE = 'Automatic backups'

export function AutoBackupCard() {
  const [settings, setSettings] = useState<AutoBackupSettings | null>(null)

  useEffect(() => {
    let live = true
    void loadPcMode().then(() => {
      if (!live || getPcMode() === 'remote') return
      // The summary is a convenience; an error here just leaves the link.
      getBackupSettings().then((s) => live && setSettings(s), () => undefined)
    })
    return () => {
      live = false
    }
  }, [])

  return (
    <Card title={TITLE} meta={settings ? settingsSummary(settings) : undefined} aria-label={TITLE} className="auto-backup-summary">
      <p className="settings-note">
        Backup schedule, folder, "Back up now", copies and restore are in one place: Library tools, Backup tab.
      </p>
      <div className="settings-actions">
        <ButtonLink href={routeHref({ name: 'library-tools', tab: 'backup' })} variant="secondary">
          Manage backups
        </ButtonLink>
      </div>
    </Card>
  )
}
